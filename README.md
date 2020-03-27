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

    long_call = alice.debug.sleep(5_000)  # sleep 5 seconds
    print('made long call')
    short_call = alice.debug.sleep(3_000)  # sleep 3 seconds
    print('made short call')

    await short_call
    print("done with short call")
    await long_call
    print('done with long call')

    # Getting a result should be as easy as calling, and waiting for the key we are after
    bob_addr = await bob.host.view().enr()
    print('BOB has ENR: ', bob_addr)

    # Print all ENR contents
    await rumor.enr.view(bob_addr)

    # Command arguments are just call arguments
    await alice.peer.connect(bob_addr)
    print("connected alice to bob!")

    # You can use either await or async-for to get data of a specific key
    async for msg in bob.peer.list().msg():
        print(f'bob peer list: {msg}')

    async for msg in alice.peer.list().msg():
        print(f'alice peer list: {msg}')

    # Or alternatively, collect all result data from the call:
    bob_addr_data = await rumor.enr.view(bob_addr)

    print("--- BOB host view data: ---")
    print("\n".join(f"{k}: {v}" for k, v in bob_addr_data.items()))

    print("Testing a Status RPC exchange")

    alice_peer_id = await alice.host.view().peer_id()

    alice_status = StatusReq(head_slot=42)
    bob_status = StatusReq(head_slot=123)

    async def alice_work(nursery: trio.Nursery) -> Call:
        print("alice: listening for status requests")
        call = alice.rpc.status.listen(raw=True)

        async def process_requests():
            async for req in call.req():
                print(f"alice: Got request: {req}")
                assert 'input_err' not in req
                # Or send back an error; await alice.rpc.status.resp.invalid_request(req['req_id'], f"hello! Your request was invalid, because: {req['input_err']}").ok
                assert req['data'] == bob_status.encode_bytes().hex()
                resp = alice_status.encode_bytes().hex()
                print(f"alice: sending response back to request {req['req_id']}: {resp}")
                await alice.rpc.status.resp.chunk.raw(req['req_id'], resp, done=True)
            print("alice: stopped listening for status requests")

        nursery.start_soon(process_requests)

        await call.started()  # wait for the stream handler to come online, there will be a "started=true" entry.
        return call

    async def bob_work():
        # Send alice a status request
        req = bob_status.encode_bytes().hex()
        print(f"bob: sending alice ({alice_peer_id}) a status request: {req}")
        resp = await bob.rpc.status.req.raw(alice_peer_id, req, raw=True)
        print(f"bob: received status response from alice: {resp}")
        chunk = resp['chunk']
        assert chunk['chunk_index'] == 0  # only 1 chunk
        assert chunk['result_code'] == 0  # success chunk
        assert chunk['data'] == alice_status.encode_bytes().hex()

    # Run tasks in a trio nursery to make them concurrent
    async with trio.open_nursery() as nursery:
        # Set up alice to listen for requests
        alice_listen_call = await alice_work(nursery)

        # Make bob send a request and check a response, after alice is set up
        await bob_work()

        # Close alice
        await alice_listen_call.cancel()


async def run_example():
    # Hook it up to your own local version of Rumor, if you like.
    # And optionally enable debug=True to be super verbose about Rumor communication.
    async with Rumor(cmd='cd ../rumor && go run .') as rumor:
        await basic_rpc_example(rumor)

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
BOB has ENR:  enr:-Iu4QJsaKuzeeK1kdN2RezOUVGw2W57Du2Js-u1uuLqzcFLkYeoEty9XPWxJ8lNGf23ZHBEUQ7tnB_HQ5Gw9phdsWZaAgmlkgnY0gmlwhMCoAE2Jc2VjcDI1NmsxoQPPxmeIkd_qGoJ7p6ckO4ZKKOnOidet-lDsRfDgSv2R5IN0Y3CCIymDdWRwgiMp
connected alice to bob!
bob peer list: 1 peers
bob peer list:    0: {16Uiu2HAmGf5dZYGcfkszFcYzhYMpkFohcVHSAsd8NBJwDDahHNQK: [/ip4/192.168.0.77/tcp/9000]}
alice peer list: 1 peers
alice peer list:    0: {16Uiu2HAmSe4DMy8dZneZUovFPAPqmbm3FWonHiabr9w3RUnnJ7Cw: [/ip4/192.168.0.77/tcp/9001]}
--- BOB host view data: ---
enode: enode://cfc6678891dfea1a827ba7a7243b864a28e9ce89d7adfa50ec45f0e04afd91e4cd3007b21b5d5012524cc28045a06efe34f03868f68da68b0d06c7cf621bd921@192.168.0.77:9001
enr: enr:-Iu4QJsaKuzeeK1kdN2RezOUVGw2W57Du2Js-u1uuLqzcFLkYeoEty9XPWxJ8lNGf23ZHBEUQ7tnB_HQ5Gw9phdsWZaAgmlkgnY0gmlwhMCoAE2Jc2VjcDI1NmsxoQPPxmeIkd_qGoJ7p6ckO4ZKKOnOidet-lDsRfDgSv2R5IN0Y3CCIymDdWRwgiMp
msg: ENR parsed successfully
multi: /ip4/192.168.0.77/tcp/9001/p2p/16Uiu2HAmSe4DMy8dZneZUovFPAPqmbm3FWonHiabr9w3RUnnJ7Cw
node_id: 00c7eeec07527edbf72dff6d84d0dcdb9f2e1eacd54372db530a515b01b6941f
peer_id: 16Uiu2HAmSe4DMy8dZneZUovFPAPqmbm3FWonHiabr9w3RUnnJ7Cw
seq: 0
xy: 93979309937351124012010597461777211511079463010981099424184477356342451737060 92808995732655724877169966925472115400857265498307667452857464010673380645153
Testing a Status RPC exchange
alice: listening for status requests
bob: sending alice (16Uiu2HAmGf5dZYGcfkszFcYzhYMpkFohcVHSAsd8NBJwDDahHNQK) a status request: 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007b00000000000000
alice: Got request: {'data': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007b00000000000000', 'from': '16Uiu2HAmSe4DMy8dZneZUovFPAPqmbm3FWonHiabr9w3RUnnJ7Cw', 'protocol': '/eth2/beacon_chain/req/status/1/ssz', 'req_id': 0}
alice: sending response back to request 0: 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002a00000000000000
bob: received status response from alice: {'chunk': {'chunk_index': 0, 'chunk_size': 84, 'data': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002a00000000000000', 'from': '16Uiu2HAmGf5dZYGcfkszFcYzhYMpkFohcVHSAsd8NBJwDDahHNQK', 'protocol': '/eth2/beacon_chain/req/status/1/ssz', 'result_code': 0}, 'msg': 'Received chunk'}
alice: stopped listening for status requests

Process finished with exit code 0
```


## License

MIT, see [LICENSE](./LICENSE) file.
