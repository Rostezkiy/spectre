"""Setup script for Spectre."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="spectre",
    version="0.1.0",
    author="Spectre Contributors",
    description="Local-first API generator via headless browser capture",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/spectre",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        "duckdb>=0.9.0",
        "pydantic>=2.0.0",
        "pyyaml>=6.0",
        "typer>=0.9.0",
        "rich>=13.0.0",
        "playwright>=1.40.0",
        "fastapi>=0.104.0",
        "uvicorn>=0.24.0",
        "python-multipart>=0.0.6",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "httpx>=0.25.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "spectre=spectre.cli:main",
        ],
    },
)
