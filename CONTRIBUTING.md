# Contributing to ITAMbox

Thank you for your interest in contributing to ITAMbox. To ensure code quality and a smooth integration workflow, please follow these guidelines.

---

## Development Setup

ITAMbox is a standard Django application. Set up your local environment as follows:

1.  **Fork and Clone the Repository:**
    ```bash
    git clone https://github.com/itambox/itambox-webapp.git
    cd itambox-webapp
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
    cd itambox
    python manage.py migrate
    python manage.py seed_data
    ```

5.  **Run the Server:**
    ```bash
    ITAMBOX_DEBUG=true python manage.py runserver
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

Any bug fixes or new features must include appropriate tests. The suite uses
`pytest` (pytest-django); run all commands from the `itambox/` directory.

> **PostgreSQL is required.** Tests need a running PostgreSQL instance on port
> `5433` — the project uses a disposable Postgres container for local testing.
> SQLite is rejected at settings load, so the suite will not run without it.

*   Run the entire test suite:
    ```bash
    cd itambox
    pytest
    ```
*   Run tests for a single application module (e.g., assets):
    ```bash
    pytest assets/tests/
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
