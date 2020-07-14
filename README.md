![](https://raw.githubusercontent.com/protolambda/pyrum/master/logo.png)

# `pyrum`

[![](https://img.shields.io/pypi/l/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/pyversions/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/status/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/implementation/pyrum.svg)](https://pypi.python.org/pypi/pyrum)

Pyrum ("Py Rumor") is a Python interface to interact with [Rumor](https://github.com/protolambda/rumor), an Eth2 networking shell.

## Usage

This interface maps async Python functions (built on Trio) to Rumor commands.

The mapping is simple:
- A command path is equal to a series of fields
- A command argument is equal to a call argument
- A command flag is equal to a call keyword argument, every `_` in the keyword is replaced with `-`

Some examples:

```
host listen --tcp=9001
->
peer.listen(tcp=9001)

peer connect /dns4/prylabs.net/tcp/30001/p2p/16Uiu2HAm7Qwe19vz9WzD2Mxn7fXd1vgHHp4iccuyq7TxwRXoAGfc
->
peer.connect('/dns4/prylabs.net/tcp/30001/p2p/16Uiu2HAm7Qwe19vz9WzD2Mxn7fXd1vgHHp4iccuyq7TxwRXoAGfc')
```

Each of these calls returns a `Call` object:
- Wait for successful call completion by awaiting the `my_call`.
- Retrieve latest data from the `my_call.data` dict
- Individual data can be retrieved by calling `await my_call.some_data_field()` to get the respective data as soon as it is available.
- If you expect multiple occurrences of the data, you can `await` multiple times, or use `async for item in my_call`

### Full example

```python
import trio
from pyrum import WebsocketConn, TCPConn, UnixConn, SubprocessConn, Rumor

from remerkleable.complex import Container
from remerkleable.byte_arrays import Bytes32, Bytes4
from remerkleable.basic import uint64


class StatusReq(Container):
    version: Bytes4
    finalized_root: Bytes32
    finalized_epoch: uint64
    head_root: Bytes32
    head_slot: uint64


async def basic_rpc_example(rumor: Rumor):
    alice = rumor.actor('alice')
    await alice.host.start()
    # Flags are keyword arguments
    await alice.host.listen(tcp=9000)
    print("started alice")

    # Concurrency in Rumor, planned with async (Trio) in Pyrum
    bob = rumor.actor('bob')
    await bob.host.start()
    await bob.host.listen(tcp=9001)
    print("started bob")

    long_call = alice.sleep('5s')  # sleep 5 seconds
    print('made long call')
    short_call = alice.sleep('3s')  # sleep 3 seconds
    print('made short call')

    await short_call
    print("done with short call")
    await long_call
    print('done with long call')

    # Getting a result should be as easy as calling, and waiting for the key we are after
    bob_addr = await bob.host.view().addr()
    print('BOB has address: ', bob_addr)

    # Command arguments are just call arguments
    await alice.peer.connect(bob_addr)
    print("connected alice to bob!")

    # You can use either await or async-for to get data of a specific key
    async for addr in bob.host.view().addr():
        print(f'bob has addr: {addr}')  # multiple addresses, but the last one matters most.

    # Optionally request more peer details
    peerdata = await alice.peer.list(details=True).peers()
    print(f'alice peer list: {peerdata}')

    print("Testing a Status RPC exchange")

    alice_peer_id = await alice.host.view().peer_id()

    alice_status = StatusReq(head_slot=42)
    bob_status = StatusReq(head_slot=123)

    async def alice_work(nursery: trio.Nursery) -> Call:
        print("alice: listening for status requests")
        call = alice.rpc.status.listen(raw=True)
        # Wait for inital completion of setup, i.e. the listener is online.
        # It will stay open in the background, until `await call.finished()`
        await call

        async def process_requests():
            # Each req object is a dict with all the latest log data at the time of completion of the step.
            async for req in call:
                print(f"alice: Got request: {req}")
                assert 'input_err' not in req
                # Or send back an error; await alice.rpc.status.resp.invalid_request(req['req_id'], f"hello! Your request was invalid, because: {req['input_err']}")
                assert req['data'] == bob_status.encode_bytes().hex()
                resp = alice_status.encode_bytes().hex()
                print(f"alice: sending response back to request {req['req_id']}: {resp}")
                await alice.rpc.status.resp.chunk.raw(req['req_id'], resp, done=True)
            print("alice: stopped listening for status requests")

        nursery.start_soon(process_requests)

        return call

    async def bob_work():
        # Send alice a status request
        req = bob_status.encode_bytes().hex()
        print(f"bob: sending alice ({alice_peer_id}) a status request: {req}")
        req_call = bob.rpc.status.req.raw(alice_peer_id, req, raw=True)
        await req_call
        # Await request to be written to stream
        await req_call.next()
        # Await first (and only) response chunk
        resp = await req_call.next()

        print(f"bob: received status response from alice: {resp}")
        assert resp['chunk_index'] == 0  # first chunk
        assert resp['result_code'] == 0  # success chunk
        assert resp['data'] == alice_status.encode_bytes().hex()

    # Run tasks in a trio nursery to make them concurrent
    async with trio.open_nursery() as nursery:
        # Set up alice to listen for requests
        alice_listen_call = await alice_work(nursery)

        # Make bob send a request and check a response, after alice is set up
        await bob_work()

        # Close listener of alice
        await alice_listen_call.cancel()


async def run_example():
    # Websockets
    # Start Rumor with websocket serving enabled, then open a connection from rumor:
    # rumor serve --ws=localhost:8010 --ws-path=/ws --ws-key=foobar
    # async with WebsocketConn(ws_url='ws://localhost:8010/ws', ws_key='foobar') as conn:

    # TCP sockets
    # rumor serve --tcp localhost:3030
    # async with TCPConn(addr='localhost', port=3030) as conn:

    # Unix domain sockets
    # rumor serve --ipc my_ipc.socket
    # async with UnixConn(socket_path='../some/path/my_ipc.socket') as conn:

    # Subprocess
    # Run it in "bare" mode so there is no shell clutter, and every Rumor output is JSON for Pyrum to parse.
    # Optionally specify your own rumor executable, for local development/modding of Rumor
    async with SubprocessConn(cmd='cd ../rumor && go run . bare --level=trace') as conn:
        # A Trio nursery hosts all the async tasks of the Rumor instance.
        async with trio.open_nursery() as nursery:
            # And optionally use Rumor(conn, nursery, debug=True) to be super verbose about Rumor communication.
            await basic_rpc_example(Rumor(conn, nursery, debug=True))

trio.run(run_example)
```

Example Output:
```
started alice
started bob
made long call
made short call
done with short call
done with long call
BOB has address:  /ip4/127.0.0.1/tcp/9001/p2p/16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu
connected alice to bob!
bob has addr: /ip4/127.0.0.1/tcp/9001/p2p/16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu
bob has addr: /ip4/192.168.0.77/tcp/9001/p2p/16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu
alice peer list: {'16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu': {'peer_id': '16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu', 'node_id': '384cbbccd148b82a96138d552e6909225d4169bfaf0b1ea27ccd10e02141187e', 'pubkey': '0287bd489f112f9d43bc04b7c561df3b93d35f1311b6d52df524321b7485e2012e', 'addrs': ['/ip4/127.0.0.1/tcp/9001', '/ip4/192.168.0.77/tcp/9001'], 'protocols': ['/p2p/id/delta/1.0.0', '/ipfs/ping/1.0.0', '/libp2p/circuit/relay/0.1.0', '/ipfs/id/1.0.0', '/ipfs/id/push/1.0.0'], 'user_agent': 'Rumor', 'protocol_version': 'ipfs/0.1.0'}}
Testing a Status RPC exchange
alice: listening for status requests
bob: sending alice (16Uiu2HAm6QTKBBzwpPGt1R8SQHHrUex3czzhd23VnbDkVztwibgD) a status request: 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007b00000000000000
alice: Got request: {'msg': 'Received request, queued it to respond to!', 'data': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007b00000000000000', 'from': '16Uiu2HAm4ZYRU2J9pmsCLnYSfmGEQSsCCAaHD5aXr2MheSUf7egu', 'protocol': '/eth2/beacon_chain/req/status/1/ssz_snappy', 'req_id': 0}
alice: sending response back to request 0: 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002a00000000000000
bob: received status response from alice: {'msg': 'Received chunk', 'chunk_index': 0, 'chunk_size': 84, 'data': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002a00000000000000', 'from': '16Uiu2HAm6QTKBBzwpPGt1R8SQHHrUex3czzhd23VnbDkVztwibgD', 'protocol': '/eth2/beacon_chain/req/status/1/ssz_snappy', 'result_code': 0}
```


## License

MIT, see [LICENSE](./LICENSE) file.
