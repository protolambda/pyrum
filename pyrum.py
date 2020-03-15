import asyncio
import json

from typing import List, Dict, Union, AsyncGenerator, Any, Callable, Coroutine
from asyncio import Future, Queue as AsyncQueue
from asyncio.subprocess import Process


CallID = str


class Rumor(object):
    calls: Dict[CallID, "Call"]
    _unique_call_id_counter: int
    _debug: bool
    rumor_process: Process
    _closed: Future

    actors: Dict[str, "Actor"]

    def __init__(self, debug: bool = False):
        self._debug = debug
        self._closed = Future()
        self.calls = {}
        self.actors = {}
        self._unique_call_id_counter = 0

    async def start(self, cmd: str = 'rumor'):
        self.rumor_process = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        loop = asyncio.get_event_loop()

        async def read_loop():
            while True:
                next_line_fut = asyncio.ensure_future(self.rumor_process.stdout.readline())
                await asyncio.wait([next_line_fut, self._closed], return_when=asyncio.FIRST_COMPLETED)
                if self._closed.done():
                    break
                line = next_line_fut.result()
                if line == b'':
                    if self._debug:
                        print("Closing Rumor debug loop")
                    return

                line = line.decode('utf-8')
                if line.endswith('\n'):
                    line = line[:-1]

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
                    if isinstance(call_id, str) and call_id.startswith('@'):
                        call_id = call_id[1:]
                        if call_id not in self.calls:
                            continue

                        call: Call = self.calls[call_id]
                        await call.on_entry(entry)
                        if call.ok.done() or call.ok.cancelled():
                            # call is over, remove it
                            del self.calls[call_id]

                # find the corresponding actor, pass on the event.
                if 'actor' in entry:
                    actor_name = entry['actor']
                    if actor_name not in self.actors:
                        self.actors[actor_name] = Actor(self, actor_name)

                    actor = self.actors[actor_name]
                    await actor.on_entry(entry)

        async def debug_loop():
            while True:
                next_line_fut = asyncio.ensure_future(self.rumor_process.stderr.readline())
                await asyncio.wait([next_line_fut, self._closed], return_when=asyncio.FIRST_COMPLETED)
                if self._closed.done():
                    break
                line = next_line_fut.result()
                line = await self.rumor_process.stderr.readline()
                if line == b'':
                    print("Closing Rumor debug loop")
                    return
                line = line.decode('utf-8')
                if line.endswith('\n'):
                    line = line[:-1]
                print(f"ERROR from Rumor: '{line}'")

        if self.debug:
            loop.create_task(debug_loop())

        loop.create_task(read_loop())

    async def stop(self):
        # Clean up, read-loops should stop
        self._closed.set_result(None)
        # Ask it nicely to stop, then wait for it to complete
        inp = self.rumor_process.stdin
        inp.write('exit\n'.encode())
        await inp.drain()
        await self.rumor_process.wait()

    def actor(self, name: str) -> "Cmd":
        if name not in self.actors:
            self.actors[name] = Actor(self, name)
        return self.actors[name]

    async def _send_to_rumor_process(self, line):
        if self._closed.done():
            raise Exception("Rumor is closed")
        inp = self.rumor_process.stdin
        if self._debug:
            print(f"Sending command to Rumor: '{line}'")
        inp.write((line + '\n').encode())
        await inp.drain()

    def make_call(self, args: List[str]) -> "Call":
        # Get a unique call ID
        call_id = f"py_${self._unique_call_id_counter}"
        self._unique_call_id_counter += 1
        # Create the call, and remember it
        cmd = ' '.join(args)
        call = Call(call_id, cmd)
        self.calls[call_id] = call
        # Send the actual command, with call ID, to Rumor
        asyncio.create_task(self._send_to_rumor_process(call_id + '> ' + cmd))
        return call

    # No actor, Rumor will just default to a default-actor.
    # But this is useful for commands that don't necessarily have any actor, e.g. debugging the contents of an ENR.
    def __getattr__(self, item) -> "Cmd":
        return Cmd(self, [item])


def args_to_call_path(*args, **kwargs) -> List[str]:
    # TODO: maybe escape values?
    return list(args) + [f'--{key.replace("_", "-")}="{value}"' for key, value in kwargs.items()]


class Actor(object):

    def __init__(self, rumor: Rumor, name: str):
        self.rumor = rumor
        self.name = name
        self.q = AsyncQueue()

    def __call__(self, *args, **kwargs) -> "Call":
        return self.rumor.make_call([f'{self.name}:'] + args_to_call_path(*args, **kwargs))

    def __getattr__(self, item) -> "Cmd":
        return Cmd(self.rumor, [f'{self.name}:'] + [item])

    async def on_entry(self, entry: Dict[str, Any]):
        await self.q.put(entry)

    async def logs(self) -> AsyncGenerator:
        """Async generator to wait for and yield each log entry, including errors"""
        while True:
            v = await self.q.get()
            if v is None:
                break
            yield v


class Cmd(object):
    def __init__(self, rumor: Rumor, path: List[str]):
        self.rumor = rumor
        self.path = path

    def __call__(self, *args, **kwargs) -> "Call":
        return self.rumor.make_call(self.path + args_to_call_path(*args, **kwargs))

    def __getattr__(self, item) -> "Cmd":
        return Cmd(self.rumor, self.path + [item])


class CallException(Exception):
    def __init__(self, call_id: CallID, err_entry: Dict[str, Any]):
        self.call_id = call_id
        self.err_entry = err_entry


IGNORED_KEYs = ['actor', 'call_id', 'level', '@success', 'time']


class Call(object):
    data: Dict[str, Any]
    _awaited_data: Dict[str, Future]
    _queued_data: Dict[str, AsyncQueue]
    cmd: str
    # Future to wait for command to complete, future raises exception if any error entry makes it first.
    ok: Future

    def __init__(self, call_id: CallID, cmd: str):
        self.data = {}
        self._awaited_data = {}
        self._queued_data = {}
        self.call_id = call_id
        self.cmd = cmd
        self.ok = Future()
        self.q = AsyncQueue()

    async def on_entry(self, entry: Dict[str, Any]):
        # Log the entry, if call is being monitored
        await self.q.put(entry)

        # Don't process the contents of the final closing message,
        # it does not contain anything useful
        if '@success' not in entry:
            # Merge in new result data, overwrite any previous data
            for k, v in entry.items():
                if k in IGNORED_KEYs:
                    continue
                self.data[k] = v

                # Add the data to a queue, so it can be listened for, even after the fact
                if k not in self._queued_data:
                    self._queued_data[k] = AsyncQueue()
                await self._queued_data[k].put(v)

                # If the data is being awaited, then finish the future
                if k in self._awaited_data:
                    f = self._awaited_data[k]
                    if not f.done():  # If we didn't have something complete it earlier
                        f.set_result(v)

        # Complete call future with exception
        if entry['level'] == 'error':
            await self._on_finish()
            self.ok.set_exception(CallException(self.call_id, entry))
            return
        # Complete call future as normal
        if '@success' in entry:  # special key, present in last log, after command completes
            await self._on_finish()
            self.ok.set_result(self.data)  # complete with the full set of latest merged data.
            return

    async def _on_finish(self):
        """Cleans up the remaining things before the call truly completes"""
        # Close all unfinished data futures with an exception
        for k, v in self._awaited_data.items():
            if not v.done():
                v.set_exception(KeyError('Never got data for key: %s' % k))
        # Close all queues by adding None sentinel.
        for k, q in self._queued_data.items():
            await q.put(None)

    async def logs(self) -> AsyncGenerator:
        """Async generator to wait for and yield each log entry, including errors"""
        while True:
            v = await self.q.get()
            yield v

    def __getattr__(self, item) -> Callable[[], Union[Coroutine, AsyncGenerator]]:
        if not isinstance(item, str):
            raise AttributeError(f"cannot handle non-str attributes: {item}")

        if item.startswith('listen_'):
            item = item[len('listen_'):]

            async def attr_async_gen():
                if item not in self._queued_data:
                    self._queued_data[item] = AsyncQueue()
                q = self._queued_data[item]
                while True:
                    v = await q.get()
                    if v is None:  # Special sentinel to effectively stop the queue
                        break
                    yield v

            return attr_async_gen
        else:
            async def attr_fut():
                # If the data is already here, then just return it
                if item in self.data:
                    return self.data[item]
                # Otherwise, register that we like to see the data
                fut = self._awaited_data[item] = Future()
                # And then wait for and return the result
                return await fut

            return attr_fut
async def basic_connect_example():
    rumor = Rumor()
    await rumor.start(cmd='cd ../rumor && go run .')  # Hook it up to your own local version of Rumor, if you like.

    alice = rumor.actor('alice')
    await alice.host.start().ok
    # Flags are keyword arguments
    await alice.host.listen(tcp=9000).ok
    print("started alice")

    bob = rumor.actor('bob')
    await bob.host.start().ok
    await bob.host.listen(tcp=9001).ok
    print("started bob")

    # Getting a result should be as easy as calling, and waiting for the key we are after
    bob_addr = await bob.host.view().enr()
    print('BOB has ENR: ', bob_addr)

    # Print all ENR contents
    await rumor.enr.view(bob_addr).ok

    # Command arguments are just call arguments
    await alice.peer.connect(bob_addr).ok
    print("connected alice to bob!")

    # When there are multiple entries containing some specific key,
    #  you can also prefix with `listen_` to get each of the values:
    async for msg in bob.peer.list().listen_msg():
        print(f'bob peer list: {msg}')

    async for msg in alice.peer.list().listen_msg():
        print(f'alice peer list: {msg}')

    # Or alternatively, collect all result data from the call:
    bob_addr_data = await rumor.enr.view(bob_addr).ok
    print("--- BOB host view data: ---")
    print("\n".join(f"{k}: {v}" for k, v in bob_addr_data.items()))

    # Close the Rumor process
    await rumor.stop()


asyncio.run(basic_connect_example())
