# aide — Terminal AI Coding Agent for OmniRoute

**`aide`** is a fast, lightweight terminal UI (TUI) coding agent built with Python, [Textual](https://textualize.io), and [HTTPX](https://python-httpx.org), designed to work seamlessly with any OpenAI-compatible **OmniRoute** endpoint.

> **Status:** Early release (`v0.1.0`) — functional core, open for community contributions, bug fixes, and new features!

---

## ✨ Key Features

* **Modern Terminal UI:** Built on `textual` for a responsive, keyboard-driven coding experience right inside your terminal.
* **Zero-Config Bootstrap (`./run.sh`):** Automatically checks your Python version, creates an isolated `.venv`, installs dependencies, and keeps them synced whenever `pyproject.toml` changes.
* **Resilient Streaming Engine:**
  * Full Server-Sent Events (SSE) streaming with support for **reasoning / thinking tokens** (`reasoning_content`) and native **tool calls**.
  * Exponential backoff with jitter and `Retry-After` header support for rate limits (`429`), timeouts, and transient `5xx` errors.
  * Graceful fallback if the gateway returns a standard non-streamed JSON response.
* **Security-First Design:**
  * Configuration files (`~/.config/aide/config.toml`) are written atomically and locked down to `0600` permissions automatically.
  * Optional `--debug-log` records API traffic in JSONL format while automatically redacting API keys and `Bearer` tokens.
* **Flexible Configuration:** Layered settings via CLI flags, `OMNIROUTE_*` environment variables, `config.toml`, or an interactive first-run setup wizard.

---

## 🚀 Quick Start

### Prerequisites
* **Python 3.11+**
* A running **OmniRoute** instance (e.g., `http://localhost:5555` or a remote endpoint)

### 1. Clone the Repository

```bash
git clone https://github.com
cd omniroute-ai-idle
chmod +x run.sh
```

### 2. Configure OmniRoute Before Launch

Create and edit the configuration file at `~/.config/aide/config.toml`:

```bash
mkdir -p ~/.config/aide
cat << 'EOF' > ~/.config/aide/config.toml
# aide configuration. OMNIROUTE_* environment variables override these values.
# This file may contain your API key - keep its permissions at 600.

base_url = "sl"
api_key = "ENTER_UOYR_API_KEY"
model = "gpt-4o-mini"
temperature = 0.2
max_tokens = 4096
timeout = 120.0
EOF
chmod 600 ~/.config/aide/config.toml
```

> **Note:** Replace `base_url` (e.g., `http://localhost:5555`), `api_key`, and `model` with your actual OmniRoute values before running. *(If you skip creating `config.toml`, running `./run.sh` will automatically launch an interactive setup wizard on first start.)*

### 3. Launch `aide`

Run in the current directory:

```bash
./run.sh
```

Or specify a target project workspace:

```bash
./run.sh /path/to/your/project
```

---

## ⚙️ Configuration Reference

Settings are loaded in the following order of precedence (highest first):
1. **Command-line flags**
2. **Environment variables** (`OMNIROUTE_*`)
3. **Config file** (`~/.config/aide/config.toml` or `$AIDE_CONFIG`)
4. **Built-in defaults**

### All Available `config.toml` Options

Location: `~/.config/aide/config.toml` (permissions `600`)

```toml
base_url = "sl"
api_key = "ENTER_UOYR_API_KEY"
model = "gpt-4o-mini"
temperature = 0.2
max_tokens = 4096
timeout = 120.0
max_retries = 4
stream_usage = true
```

### Environment Variables

You can override any setting via environment variables without editing `config.toml`:

```bash
export OMNIROUTE_BASE_URL="http://localhost:5555"
export OMNIROUTE_API_KEY="ENTER_UOYR_API_KEY"
export OMNIROUTE_MODEL="gpt-4o-mini"
```

---

## 🛠️ CLI Options

```text
usage: aide [-h] [--config FILE] [--model MODEL] [--temperature TEMPERATURE]
            [--max-tokens MAX_TOKENS] [--reconfigure] [--debug-log]
            [--debug-log-file FILE] [--version]
            [workspace]
```

| Flag | Description |
| :--- | :--- |
| `workspace` | Target project directory for the agent (default: current directory `.`) |
| `--reconfigure` | Re-run the interactive setup wizard to update endpoint, key, or model |
| `--model MODEL` | Override the configured model for the current session |
| `--temperature FLOAT` | Override sampling temperature (`0.0` – `2.0`) |
| `--max-tokens INT` | Override maximum tokens per response |
| `--debug-log` | Save API traffic (with secrets redacted) to `~/.local/state/aide/debug.log` |
| `--debug-log-file FILE` | Write redacted debug logs to a custom file path |

---

## 🧪 Running Tests

To install development dependencies and run the test suite:

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

---

## 🤝 Contributing

Contributions, forks, and pull requests are very welcome! Whether you want to fix bugs, improve cross-platform support (macOS/Windows), or take ownership of new features, feel free to jump in:

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/awesome-improvement`)
3. Commit your changes and run `pytest`
4. Open a Pull Request

### Ideas for Contributions
* [ ] Cross-platform `md5` fallback in `run.sh` for macOS compatibility
* [ ] Guard `os.fchmod` calls in `config.py` and `debuglog.py` for Windows compatibility
* [ ] Additional workspace tools, diff previews, and session management improvements

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
