from setuptools import setup

with open("README.md", "rt", encoding="utf8") as f:
    readme = f.read()

setup(
    name="pyrum",
    description="Python interface for Rumor - Eth2 networking shell",
    version="0.1.0",
    long_description=readme,
    long_description_content_type="text/markdown",
    author="protolambda",
    author_email="proto+pip@protolambda.com",
    url="https://github.com/protolambda/pyrum",
    python_requires=">=3.8, <4",
    license="MIT",
    packages=[],
    py_modules=["pyrum"],
    tests_require=[],
    install_requires=[
        "trio==0.13.0"
    ],
    include_package_data=False,
    keywords=["rumor", "networking", "libp2p" "eth2"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Operating System :: OS Independent",
    ],
)
