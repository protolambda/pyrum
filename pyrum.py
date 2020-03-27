import json

from typing import List, Dict, Any, Callable, Tuple
import signal
import trio
import subprocess

# After a MAX_MSG_BUFFER_SIZE unprocessed messages, there will be back-pressure on the producer.
MAX_MSG_BUFFER_SIZE = 1000

CallID = str
_RECEIVE_SIZE = 4096  # pretty arbitrary


# From Trio GitHub issue: https://github.com/python-trio/trio/issues/796#issuecomment-471428274
class TerminatedFrameReceiver:
    """Parse frames out of a Trio stream, where each frame is terminated by a
    fixed byte sequence.

    For example, you can parse newline-terminated lines by setting the
    terminator to b"\n".

    This uses some tricks to protect against denial of service attacks:

    - It puts a limit on the maximum frame size, to avoid memory overflow; you
    might want to adjust the limit for your situation.

    - It uses some algorithmic trickiness to avoid "slow loris" attacks. All
      algorithms are amortized O(n) in the length of the input.

    """
    def __init__(self, stream, terminator, max_frame_length=16384):
        self.stream = stream
        self.terminator = terminator
        self.max_frame_length = max_frame_length
        self._buf = bytearray()
        self._next_find_idx = 0

    async def receive(self):
        while True:
            terminator_idx = self._buf.find(
                self.terminator, self._next_find_idx
            )
            if terminator_idx < 0:
                # no terminator found
                if len(self._buf) > self.max_frame_length:
                    raise ValueError("frame too long")
                # next time, start the search where this one left off
                self._next_find_idx = max(0, len(self._buf) - len(self.terminator) + 1)
                # add some more data, then loop around
                more_data = await self.stream.receive_some(_RECEIVE_SIZE)
                if more_data == b"":
                    if self._buf:
                        raise ValueError("incomplete frame")
                    raise trio.EndOfChannel
                self._buf += more_data
            else:
                # terminator found in buf, so extract the frame
                frame = self._buf[:terminator_idx]
                # Update the buffer in place, to take advantage of bytearray's
                # optimized delete-from-beginning feature.
                del self._buf[:terminator_idx+len(self.terminator)]
                # next time, start the search from the beginning
                self._next_find_idx = 0
                return frame

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except trio.EndOfChannel:
            raise StopAsyncIteration


class Rumor(object):
    calls: Dict[CallID, "Call"]
    _unique_call_id_counter: int
    _cmd: str
    _debug: bool
    _nursery: trio.Nursery
    _exit_nursery: Any
    rumor_process: trio.Process
    _to_rumor: trio.MemorySendChannel

    actors: Dict[str, "Actor"]

    def __init__(self, cmd: str = 'rumor', debug: bool = False):
        self._debug = debug
        self._cmd = cmd
        self.calls = {}
        self.actors = {}
        self._unique_call_id_counter = 0

    async def __aenter__(self) -> "Rumor":
        nursery_mng = trio.open_nursery()
        self._nursery = await nursery_mng.__aenter__()
        self._exit_nursery = nursery_mng.__aexit__

        self.rumor_process = await trio.open_process(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )

        self._to_rumor, _for_rumor = trio.open_memory_channel(20)  # buffer information to be sent to rumor

        async def write_loop():
            async for line in _for_rumor:
                if self._debug:
                    print('Sending line to Rumor:' + str(line))
                inp: trio.abc.SendStream = self.rumor_process.stdin
                await inp.send_all((line + '\n').encode())

        async def read_loop():
            async for line in TerminatedFrameReceiver(self.rumor_process.stdout, b'\n'):
                if self._debug:
                    print('Received line from Rumor:' + str(line))

                try:
                    entry: Dict[str, Any] = json.loads(line)
                except Exception as e:
                    print(f"json decoding exception on input '{line}': {e}")
                    continue

                # find the corresponding call, pass on the event.
                if 'call_id' in entry:
                    call_id = entry['call_id']
                    if isinstance(call_id, str) and call_id.startswith('py_'):
                        if call_id not in self.calls:
                            continue

                        call: Call = self.calls[call_id]
                        if call.ok.is_set():
                            # call is over, remove it
                            del self.calls[call_id]
                        else:
                            await call.send_ch.send(entry)

        async def debug_loop():
            async for line in TerminatedFrameReceiver(self.rumor_process.stderr, b'\n'):
                print(f"ERROR from Rumor: '{line}'")

        if self.debug:
            self._nursery.start_soon(debug_loop)

        self._nursery.start_soon(write_loop)
        self._nursery.start_soon(read_loop)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._nursery.cancel_scope.cancel()
        # Stop all tasks that use the process
        await self._exit_nursery(exc_type, exc_val, exc_tb)
        # Ask it nicely to stop
        self.rumor_process.send_signal(signal.SIGINT)
        # Wait for it to complete
        await self.rumor_process.aclose()

    def actor(self, name: str) -> "Actor":
        if name not in self.actors:
            self.actors[name] = Actor(self, name)
        return self.actors[name]

    def make_call(self, actor: str, args: List[str]) -> "Call":
        # Get a unique call ID
        call_id = f"py_${self._unique_call_id_counter}"
        self._unique_call_id_counter += 1
        # Create the call, and remember it
        cmd = ' '.join(map(str, args))
        call = Call(self, call_id, cmd)
        self.calls[call_id] = call
        # Send the actual command, with call ID, to Rumor
        self._nursery.start_soon(self._to_rumor.send, f'{call_id}> {actor}: bg {cmd}')
        return call

    async def cancel_call(self, call_id: CallID):
        # Send the actual command, with call ID, to Rumor
        await self._to_rumor.send(f'{call_id}> cancel')

    # No actor, Rumor will just default to a default-actor.
    # But this is useful for commands that don't necessarily have any actor, e.g. debugging the contents of an ENR.
    def __getattr__(self, item) -> "Cmd":
        return Cmd(self, 'DEFAULT_ACTOR', [item])


def args_to_call_path(*args, **kwargs) -> List[str]:
    # TODO: maybe escape values?
    return [(f'"{value}"' if isinstance(value, str) else str(value)) for value in args] + [f'--{key.replace("_", "-")}="{value}"' for key, value in kwargs.items()]


class Actor(object):

    def __init__(self, rumor: Rumor, name: str):
        self.rumor = rumor
        self.name = name

    def __getattr__(self, item) -> "Cmd":
        return Cmd(self.rumor, self.name, [item])


class Cmd(object):
    def __init__(self, rumor: Rumor, actor: str, path: List[str]):
        self.rumor = rumor
        self.actor = actor
        self.path = path

    def __call__(self, *args, **kwargs) -> "Call":
        return self.rumor.make_call(self.actor, self.path + args_to_call_path(*args, **kwargs))

    def __getattr__(self, item) -> "Cmd":
        return Cmd(self.rumor, self.actor, self.path + [item.replace("_", "-")])


class CallException(Exception):
    def __init__(self, call_id: CallID, err_entry: Dict[str, Any]):
        self.call_id = call_id
        self.err_entry = err_entry


IGNORED_KEYs = ['actor', 'call_id', 'level', '@success', 'time']


class AwaitableReadChannel(object):
    recv: trio.MemoryReceiveChannel

    def __init__(self, recv):
        self.recv = recv

    def __aiter__(self):
        return self.recv.__aiter__()

    def __await__(self):
        return self.recv.receive().__await__()


class Call(object):
    rumor: Rumor
    data: Dict[str, Any]  # merge of all data so far
    _awaited_data: Dict[str, Tuple[trio.MemorySendChannel, trio.MemoryReceiveChannel]]  # data that is expected
    _send_ch: trio.MemorySendChannel
    ok: trio.Event  # event when the call is finished
    call_id: CallID
    cmd: str
    all: AwaitableReadChannel  # to listen to all log result entries
    err: trio.Event  # event when the call encounters any error (the listener may decide to ignore)

    def __init__(self, rumor: Rumor, call_id: CallID, cmd: str):
        self.rumor = rumor
        self.data = {}
        self._awaited_data = {}
        self.call_id = call_id
        self.cmd = cmd
        self.ok = trio.Event()
        self.err = trio.Event()
        recv_ch: trio.MemoryReceiveChannel
        self.send_ch, recv_ch = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
        send_all: trio.MemorySendChannel
        send_all, recv_all = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
        self.all = AwaitableReadChannel(recv_all)

        async def process_messages():
            async for entry in recv_ch:
                if entry['level'] == 'error':
                    self.err.set()
                if '@success' in entry:
                    self.ok.set()
                    await send_all.aclose()
                    await recv_ch.aclose()
                    for (sch, rch) in self._awaited_data.values():
                        await sch.aclose()
                        await rch.aclose()
                    return
                for k, v in entry.items():
                    # If the data is being awaited, then push it into the channel
                    if k not in self._awaited_data:
                        self._awaited_data[k] = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)

                    send_ch, _ = self._awaited_data[k]
                    await send_ch.send(v)
                    # Merge in new result data, overwrite any previous data
                    if k not in IGNORED_KEYs:
                        self.data[k] = v
                await send_all.send(entry)

        rumor._nursery.start_soon(process_messages)

    async def cancel(self):
        await self.rumor.cancel_call(self.call_id)

    async def _result(self) -> Dict[str, Any]:
        await self.ok.wait()
        return self.data

    def __await__(self):
        return self._result().__await__()

    def __getattr__(self, item) -> Callable[[], AwaitableReadChannel]:
        if not isinstance(item, str):
            raise AttributeError(f"cannot handle non-str attributes: {item}")

        def attr_ch() -> AwaitableReadChannel:
            # check if we already have a channel open for this item
            if item in self._awaited_data:
                _, recv = self._awaited_data[item]
                return AwaitableReadChannel(recv)

            # Otherwise, register that we like to see the data and return the listen channel for it
            send, recv = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
            self._awaited_data[item] = (send, recv)
            return AwaitableReadChannel(recv)

        return attr_ch
