import json

from typing import List, Dict, Any, Callable, Tuple, Protocol, Coroutine
import signal
import trio
import subprocess

from trio_websocket import connect_websocket_url, WebSocketConnection as TrioWS

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
                del self._buf[:terminator_idx + len(self.terminator)]
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


class RumorConn(Protocol):

    async def __aenter__(self) -> "RumorConn":
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        ...

    def __aiter__(self):
        return self

    # Get next line
    async def __anext__(self) -> str:
        ...

    async def send_line(self, line: str):
        ...

    async def close(self):
        ...


class SubprocessConn(RumorConn):
    rumor_process: trio.Process
    input_reader: TerminatedFrameReceiver
    _cmd: str

    def __init__(self, cmd: str = 'rumor bare'):
        self._cmd = cmd

    async def __aenter__(self) -> "SubprocessConn":
        self.rumor_process = await trio.open_process(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )
        self.input_reader = TerminatedFrameReceiver(self.rumor_process.stdout, b'\n')
        return self

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return (await self.input_reader.__anext__()).decode("utf-8")

    async def send_line(self, line: str):
        inp: trio.abc.SendStream = self.rumor_process.stdin
        await inp.send_all((line + '\n').encode("utf-8"))

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Ask it nicely to stop
        self.rumor_process.send_signal(signal.SIGINT)
        # Wait for it to complete
        await self.rumor_process.aclose()


class BaseSocketConn(RumorConn):
    socket: trio.SocketStream
    input_reader: TerminatedFrameReceiver
    _cmd: str

    async def __aenter__(self) -> "BaseSocketConn":
        raise NotImplementedError

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return (await self.input_reader.__anext__()).decode("utf-8")

    async def send_line(self, line: str):
        inp: trio.abc.SendStream = self.socket
        await inp.send_all((line + '\n').encode("utf-8"))

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Wait for it to complete
        await self.socket.aclose()


class UnixConn(BaseSocketConn):
    _socket_path: str

    def __init__(self, socket_path: str = 'example.socket'):
        self._socket_path = socket_path

    async def __aenter__(self) -> "UnixConn":
        self.socket = await trio.open_unix_socket(self._socket_path)
        self.input_reader = TerminatedFrameReceiver(self.socket, b'\n')
        return self


class TCPConn(BaseSocketConn):
    _addr: str
    _port: int

    def __init__(self, addr: str = 'localhost', port: int = 3030):
        self._addr = addr
        self._port = port

    async def __aenter__(self) -> "TCPConn":
        self.socket = await trio.open_tcp_stream(self._addr, self._port)
        self.input_reader = TerminatedFrameReceiver(self.socket, b'\n')
        return self


class WebsocketConn(RumorConn):
    ws: TrioWS
    _ws_url: str
    _ws_api_key: str
    _exit_nursery: Any

    def __init__(self, ws_url: str = 'ws://localhost:8000/ws', ws_key: str = ''):
        self._ws_url = ws_url
        self._ws_api_key = ws_key

    async def __aenter__(self) -> "WebsocketConn":
        nursery_mng = trio.open_nursery()
        # Open the websocket with a trio nursery that is maintained by hand,
        # as the regular open_websocket_url has some weird async-generator context-manager Trio issue.
        self._nursery = await nursery_mng.__aenter__()
        self._exit_nursery = nursery_mng.__aexit__
        headers = []
        if self._ws_api_key != "":
            headers.append(('X-Api-Key'.encode(), self._ws_api_key.encode()))
        try:
            ws_opener = await connect_websocket_url(self._nursery, self._ws_url, extra_headers=headers)
            self.ws = await ws_opener.__aenter__()
        except trio.Cancelled:
            raise Exception("failed to open websocket, check address and ws api key")
        return self

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.ws.get_message()

    async def send_line(self, line: str):
        await self.ws.send_message(line)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.ws.__aexit__(exc_type, exc_val, exc_tb)
        await self._exit_nursery(exc_type, exc_val, exc_tb)


class Rumor(object):
    calls: Dict[CallID, "Call"]
    _unique_call_id_counter: int
    _debug: bool
    _rumor_conn: RumorConn
    _nursery: trio.Nursery
    _to_rumor: trio.MemorySendChannel

    actors: Dict[str, "Actor"]

    def __init__(self, conn: RumorConn, nursery: trio.Nursery, debug: bool = False):
        self._debug = debug
        self._nursery = nursery
        self._rumor_conn = conn
        self.calls = {}
        self.actors = {}
        self._unique_call_id_counter = 0

        self._to_rumor, _for_rumor = trio.open_memory_channel(20)  # buffer information to be sent to rumor

        async def write_loop():
            async for line in _for_rumor:
                if self._debug:
                    print('Sending line to Rumor:' + str(line))
                await self._rumor_conn.send_line(line)

        async def read_loop():
            async for line in self._rumor_conn:
                line = str(line)
                if self._debug:
                    print('Received line from Rumor:' + line)

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
                        await call.send_ch.send(entry)

                        if '__freed' in entry:
                            # call is over, remove it
                            del self.calls[call_id]

        self._nursery.start_soon(write_loop)
        self._nursery.start_soon(read_loop)

    def actor(self, name: str) -> "Actor":
        if name not in self.actors:
            self.actors[name] = Actor(self, name)
        return self.actors[name]

    def make_call(self, actor: str, args: List[str]) -> "Call":
        # Get a unique call ID
        call_id = f"py_{self._unique_call_id_counter}"
        self._unique_call_id_counter += 1
        # Create the call, and remember it
        cmd = ' '.join(map(str, args))
        call = Call(self, call_id, cmd)
        self.calls[call_id] = call
        # Send the actual command, with call ID, to Rumor
        # Trace level is enabled so we get the call data (when it is done, frees, steps, etc)
        # With "&" it will go into the background, so we can start firing new commands immediately.
        self._nursery.start_soon(self._to_rumor.send, f'_{call_id} {actor}: lvl_trace {cmd} &')
        return call

    async def cancel_call(self, call_id: CallID):
        await self._to_rumor.send(f'_{call_id} cancel &')

    async def next_call(self, call_id: CallID):
        await self._to_rumor.send(f'_{call_id} next &')

    # No actor, Rumor will just default to a default-actor.
    # But this is useful for commands that don't necessarily have any actor, e.g. debugging the contents of an ENR.
    def __getattr__(self, item) -> "Cmd":
        return Cmd(self, 'DEFAULT_ACTOR', [item])


def args_to_call_path(*args, **kwargs) -> List[str]:
    # TODO: maybe escape values?
    return [(f'"{value}"' if isinstance(value, str) else str(value)) for value in args] + \
           [f'--{key.replace("_", "-")}="{value}"' for key, value in kwargs.items()]


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


IGNORED_KEYs = ['actor', 'call_id', 'level', '__success', '__freed', 'time']


class AwaitableReadChannel(object):
    recv: trio.MemoryReceiveChannel

    def __init__(self, recv):
        self.recv = recv

    def __aiter__(self):
        return self.recv.__aiter__()

    def __await__(self):
        return self.recv.receive().__await__()


class StepChannel(object):
    next: Callable[[], Coroutine]

    def __init__(self, next):
        self.next = next

    async def __anext__(self):
        try:
            return await self.next()
        except Exception:
            print("stopping iteration!")
            raise StopAsyncIteration


class Call(object):
    rumor: Rumor
    data: Dict[str, Any]  # merge of all data so far
    _awaited_data: Dict[str, Tuple[trio.MemorySendChannel, trio.MemoryReceiveChannel]]  # data that is expected
    _send_ch: trio.MemorySendChannel
    ok: trio.Event  # event when the call is done (just its setup if long-running)
    freed: trio.Event  # event when the call is completely finished and freed
    call_id: CallID
    cmd: str
    all: AwaitableReadChannel  # to listen to all log result entries
    steps: AwaitableReadChannel
    err: trio.Event  # event when the call encounters any error (the listener may decide to ignore)

    def __init__(self, rumor: Rumor, call_id: CallID, cmd: str):
        self.rumor = rumor
        self.data = {}
        self._awaited_data = {}
        self.call_id = call_id
        self.cmd = cmd
        self.ok = trio.Event()
        self.freed = trio.Event()
        self.err = trio.Event()
        recv_ch: trio.MemoryReceiveChannel
        self.send_ch, recv_ch = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
        send_all: trio.MemorySendChannel
        send_all, recv_all = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
        self.all = AwaitableReadChannel(recv_all)
        send_steps: trio.MemorySendChannel
        send_steps, recv_steps = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)
        self.steps = AwaitableReadChannel(recv_steps)

        async def process_messages():
            async for entry in recv_ch:
                if entry['level'] == 'error':
                    self.err.set()
                if '__success' in entry:  # Make the simple direct "await" complete as soon as the setup part is done.
                    self.ok.set()
                    continue

                if '__step' in entry:  # Upon a step completion, send a copy of the current data to the step channel.
                    # shallow-copy to not change data reported in the step async.
                    await send_steps.send({**self.data})
                    continue

                if '__freed' in entry:  # Stop proxying logs/data when the command ends (including its background tasks)
                    self.freed.set()
                    await send_all.aclose()
                    await send_steps.aclose()
                    await recv_steps.aclose()
                    await self.send_ch.aclose()
                    await recv_ch.aclose()
                    for (sch, rch) in self._awaited_data.values():
                        await sch.aclose()
                        await rch.aclose()
                    return

                for k, v in entry.items():
                    # Merge in new result data, overwrite any previous data
                    if k not in IGNORED_KEYs:
                        # If the data is being awaited, then push it into the channel
                        if k not in self._awaited_data:
                            self._awaited_data[k] = trio.open_memory_channel(MAX_MSG_BUFFER_SIZE)

                        send_ch, _ = self._awaited_data[k]
                        await send_ch.send(v)

                        self.data[k] = v

                await send_all.send(entry)

        rumor._nursery.start_soon(process_messages)

    async def cancel(self):
        await self.rumor.cancel_call(self.call_id)

    async def next(self):
        # Wait for call to get started first (second call will not block)
        await self.ok.wait()
        # Ask for next step
        await self.rumor.next_call(self.call_id)
        # Wait for next step
        return await self.steps

    # TODO: aiter that steps through call by repeatedly calling next, until call is finished.

    async def finished(self) -> Dict[str, Any]:
        await self.freed.wait()
        return self.data

    async def _result(self) -> Dict[str, Any]:
        await self.ok.wait()
        return self.data

    def __await__(self):
        return self._result().__await__()

    def __aiter__(self):
        return StepChannel(self.next)

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
