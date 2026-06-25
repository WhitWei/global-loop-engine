# Contributing to Global Loop Engine

First off, thank you for considering contributing to Global Loop Engine! It's people like you that make the open-source community such an amazing place to learn, inspire, and create.

## 🚀 Development Setup

1. **Fork and Clone**
   Fork the repository to your GitHub account and clone it to your local machine:
   ```bash
   git clone https://github.com/your-username/global-loop-engine.git
   cd global-loop-engine
   ```

2. **Environment Configuration**
   Copy the example environment variables:
   ```bash
   cp .env.example .env
   ```

3. **Install Dependencies**
   It is recommended to use a virtual environment. Install the engine and its development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

## 🧪 Testing

We use `pytest` to ensure the integrity of the loop engine's execution paths and sandbox behavior.

Before submitting a Pull Request, please ensure all tests pass:
```bash
pytest examples/test_token_bucket.py -v
```

## 📝 Pull Request Process

1. **Create a branch** for your feature or bug fix: `git checkout -b feature/your-feature-name`.
2. **Commit your changes**: Ensure your commit messages are descriptive and follow conventional commits (e.g., `feat(engine): add new validation node`).
3. **Push to your fork**: `git push origin feature/your-feature-name`.
4. **Open a Pull Request**: Submit the PR against the `main` branch of the upstream repository.

## 🐛 Bug Reports & Feature Requests

Please use the GitHub Issue tracker to report bugs or suggest new features. Provide as much context as possible, including logs, operating system, and Python version.
