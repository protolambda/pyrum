![](https://raw.githubusercontent.com/protolambda/pyrum/master/logo.png)

# `pyrum`

[![](https://img.shields.io/pypi/l/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/pyversions/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/status/pyrum.svg)](https://pypi.python.org/pypi/pyrum) [![](https://img.shields.io/pypi/implementation/pyrum.svg)](https://pypi.python.org/pypi/pyrum)

Pyrum ("Py Rumor") is a Python interface to interact with [Rumor](https://github.com/protolambda/rumor), an Eth2 networking shell.

This interface maps Rumor commands to async python functions.

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
- Wait for successful call completion by awaiting the `my_call.ok` future.
- Retrieve latest data from the `my_call.data` dict
- Individual data can be retrieved by calling `await my_call.some_data_field()` to get the respective data as soon as it is available.

Full example:

```python
from pyrum import Rumor
import asyncio


async def basic_connect_example():
    rumor = Rumor()
    await rumor.start(cmd='cd ../rumor && go run .')  # Hook it up to your own local version of Rumor, if you like.

    alice = rumor.actor('alice')
    await alice.host.start().ok
    # Flags are keyword arguments
    await alice.host.listen(tcp=9000).ok
    print("started alice")

    # Concurrency in Rumor, planned with async in Pyrum
    bob = rumor.actor('bob')
    await bob.host.start().ok
    await bob.host.listen(tcp=9001).ok
    print("started bob")

    long_call = alice.debug.sleep(5_000).ok  # sleep 5 seconds
    print('made long call')
    short_call = alice.debug.sleep(3_000).ok  # sleep 3 seconds
    print('made short call')

    await short_call
    print("done with short call")
    await long_call
    print('done with long call')

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
```

Example Output:
```
started alice
started bob
made long call
made short call
done with short call
done with long call
BOB has ENR:  enr:-Iu4QDJZSANnGPSG2pcOC4DQWJIVjNSPKflgUrBKC62LTHX7faPJDiGbnhPhMcJgBW5FQUGVADtzgXwZnYBYWu60o92AgmlkgnY0gmlwhMCoAE2Jc2VjcDI1NmsxoQLvYiwojQCdzUu2cJRwPDNT50qQMahVG9cT1s8ReTHMSoN0Y3CCIymDdWRwgiMp
connected alice to bob!
bob peer list: 1 peers
bob peer list:    0: {16Uiu2HAm69HEANtD7weTtc3ogkeAfez59jN77AjrKurEV8t26JFY: [/ip4/192.168.0.77/tcp/9000]}
alice peer list: 1 peers
alice peer list:    0: {16Uiu2HAmBY8F78MRrsEQCChAdTswmoVGSuQBuxVjy3aPtPxs2bbf: [/ip4/192.168.0.77/tcp/9001]}
--- BOB host view data: ---
enode: enode://ef622c288d009dcd4bb67094703c3353e74a9031a8551bd713d6cf117931cc4a486aec036d6e1e341982d13c09aae879899875d8913d826ef57847399e74776c@192.168.0.77:9001
enr: enr:-Iu4QDJZSANnGPSG2pcOC4DQWJIVjNSPKflgUrBKC62LTHX7faPJDiGbnhPhMcJgBW5FQUGVADtzgXwZnYBYWu60o92AgmlkgnY0gmlwhMCoAE2Jc2VjcDI1NmsxoQLvYiwojQCdzUu2cJRwPDNT50qQMahVG9cT1s8ReTHMSoN0Y3CCIymDdWRwgiMp
msg: ENR parsed successfully
multi: /ip4/192.168.0.77/tcp/9001/p2p/16Uiu2HAmBY8F78MRrsEQCChAdTswmoVGSuQBuxVjy3aPtPxs2bbf
node_id: 161b281f870f3ac0e337d5b693ed3403329e67c51e923d2e7c7c4bd3e6a6856a
peer_id: 16Uiu2HAmBY8F78MRrsEQCChAdTswmoVGSuQBuxVjy3aPtPxs2bbf
seq: 0
xy: 108276226593835360659557713003549688209365448159795919906582200744288296488010 32755439791403710978696135607749543911669765691070631861819210454356664285036
```

## License

MIT, see [LICENSE](./LICENSE) file.
