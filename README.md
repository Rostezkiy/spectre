# Spectre: Local-First API Generator

Spectre uses a headless browser to intercept structured data (JSON/XHR) from dynamic websites, normalizes it into stable resources, stores it efficiently, and serves it via a local, versioned REST API.

## Key Features

- **Passive Observation**: Intercept network traffic, not DOM parsing
- **Offline First**: Serve captured data from local DuckDB database
- **Auto-Discovery**: Automatically group similar requests into "Resources"
- **Content-Addressable Storage**: Deduplicate JSON bodies using SHA256 hashes
- **Dynamic API**: Generate REST endpoints based on captured resources

## Architecture

```
┌─────────────────┐    Capture    ┌──────────────┐    Store    ┌─────────────┐
│   Headless      │───────────────▶│   Watcher    │────────────▶│   DuckDB    │
│   Browser       │                │              │             │             │
└─────────────────┘                └──────────────┘             └─────────────┘
                                                                        │
┌─────────────────┐    Serve       ┌──────────────┐    Query         │
│   Client        │◀───────────────│   FastAPI    │◀─────────────────┘
│   Application   │                │   Server     │
└─────────────────┘                └──────────────┘
```

## Installation

### From source

```bash
# Clone repository
git clone <repository>
cd spectre

# Create virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install
```

### Using pip (development)

```bash
pip install -e .
```

After installation, you can run Spectre in two ways:

1. Using the installed `spectre` command (if installed with `pip install -e .` or `pip install .`):
   ```bash
   spectre --help
   ```
2. Directly via Python module (recommended for development or when not installed):
   ```bash
   python -m spectre.cli --help
   ```

The following examples use the `python -m spectre.cli` syntax, but you can replace it with `spectre` if the package is installed.

## Quick Start

1. **Initialize the database**:
   ```bash
   python -m spectre.cli db-init
   ```

2. **Capture data** from a website that serves JSON APIs:
   ```bash
   python -m spectre.cli watch https://jsonplaceholder.typicode.com
   ```
   Navigate in the browser (if visible) or let the headless browser load the page. Press `Ctrl+C` to stop.

3. **Analyze** captured URLs and generate resource suggestions:
   ```bash
   python -m spectre.cli analyze
   ```

4. **Generate configuration** (optional):
   ```bash
   python -m spectre.cli analyze --output spectre.yaml
   ```
   Edit `spectre.yaml` to adjust resource names and patterns.

5. **Start the API server**:
   ```bash
   python -m spectre.cli serve
   ```
   The server will be available at `http://localhost:8000`.

6. **Query your captured data**:
   ```bash
   curl http://localhost:8000/api/posts
   ```

## Usage

### 1. Initialize database
```bash
python -m spectre.cli db-init
```

### 2. Capture data
```bash
python -m spectre.cli watch <URL> [--session-id <id>] [--visible]
```

Options:
- `--session-id`, `-s`: Session identifier for grouping captures.
- `--visible`: Run browser with a visible window (default is headless).

### 3. Analyze captured data
```bash
python -m spectre.cli analyze [--generate-config] [--output spectre.yaml] [--limit 1000]
```

Options:
- `--generate-config`, `-g`: Generate YAML configuration and print to stdout.
- `--output`, `-o`: Write YAML configuration to a file.
- `--limit`, `-l`: Maximum distinct URLs to analyze (default 1000).

### 4. Start API server
```bash
python -m spectre.cli serve [--host 0.0.0.0] [--port 8000] [--reload]
```

Options:
- `--host`, `-h`: Bind address (default 0.0.0.0).
- `--port`, `-p`: Port to listen on (default 8000).
- `--reload`: Enable auto‑reload for development.

### 5. Clean up old captures
```bash
python -m spectre.cli clean --older-than 30 [--yes]
```

Deletes captures older than the specified number of days and removes orphaned blobs. Use `--yes` to skip confirmation.

### 6. Show version
```bash
python -m spectre.cli version
```

## Docker Deployment

```bash
docker-compose up --build
```

The API will be available at http://localhost:8000

## Configuration

Create `spectre.yaml`:

```yaml
project: "my_target_site"
base_url: "https://example.com"

resources:
  - name: "products"
    pattern: ".*/api/v1/products$"
    method: "GET"
  - name: "product_details"
    pattern: ".*/api/v1/products/[0-9]+$"
    method: "GET"
```

The configuration file is optional; Spectre will work with default settings.

### Running tests

```bash
pytest spectre/tests/
```

### Project structure

```
spectre/
├── __init__.py
├── cli.py                 # Typer CLI entry point
├── config.py              # Environment and YAML config loading
├── database.py            # DuckDB connection and migrations
├── core/
│   ├── __init__.py
│   ├── watcher.py         # Playwright capture logic
│   ├── analyzer.py        # URL heuristic analysis
│   └── models.py          # Pydantic schemas
├── server/
│   ├── __init__.py
│   ├── main.py            # FastAPI app
│   └── routes.py          # API endpoints
└── tests/
    ├── __init__.py
    ├── test_database.py
    ├── test_watcher.py
    └── test_analyzer.py
```
