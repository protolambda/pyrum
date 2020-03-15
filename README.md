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
```

## License

MIT, see [LICENSE](./LICENSE) file.
