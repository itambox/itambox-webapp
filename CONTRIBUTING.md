# Contributing to AssetBox

Thank you for your interest in contributing to AssetBox. To ensure code quality and a smooth integration workflow, please follow these guidelines.

---

## Development Setup

AssetBox is a standard Django application. Set up your local environment as follows:

1.  **Fork and Clone the Repository:**
    ```bash
    git clone https://github.com/your-username/assetbox-webapp.git
    cd assetbox-webapp
    ```

2.  **Initialize a Virtual Environment:**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # Windows: .venv\Scripts\activate
    ```

3.  **Install Development Dependencies:**
    ```bash
    pip install -r requirements.txt
    # (Optional) Install optional postgres, redis, and dev dependencies
    pip install -e .[postgres,redis,dev]
    ```

4.  **Database Migration & Seeding:**
    ```bash
    cd assetbox
    python manage.py migrate
    python manage.py seed_data
    ```

5.  **Run the Server:**
    ```bash
    ASSETBOX_DEBUG=true python manage.py runserver
    ```

---

## Code Quality & Standards

We enforce styling and formatting rules to keep the codebase clean.

### Pre-commit Hooks

We use `pre-commit` to automate local code quality checks. Installing the hooks ensures your code is linted before each commit.

1.  Install pre-commit:
    ```bash
    pip install pre-commit
    ```
2.  Register pre-commit with git:
    ```bash
    pre-commit install
    ```

Every time you run `git commit`, standard formatters and linters will run automatically on the modified files.

---

## Testing

Any bug fixes or new features must include appropriate tests.

*   Run the entire test suite:
    ```bash
    python manage.py test
    ```
*   Run tests for a single application module (e.g., assets):
    ```bash
    python manage.py test assets
    ```

---

## Pull Request Guidelines

1.  **Create a Branch:** Create a clean branch from `main` or `master` using a descriptive name:
    ```bash
    git checkout -b feature/your-feature-name
    # or
    git checkout -b bugfix/issue-description
    ```
2.  **Keep Commits Focused:** Write clear, concise commit messages. Avoid bundling unrelated changes into a single commit.
3.  **Submit the PR:** Push your branch to your fork and open a Pull Request. Fill out the description template fully.
