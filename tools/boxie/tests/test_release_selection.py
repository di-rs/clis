"""Only each application's locked runtime dependency closure triggers rebuilding."""

from boxie_infra.release_selection import dependency_closure


def test_locked_closure_ignores_other_app_and_dev_dependencies() -> None:
    lock = {
        "package": [
            {
                "name": "boxie",
                "version": "1",
                "dependencies": [{"name": "httpx"}],
                "dev-dependencies": {"dev": [{"name": "pytest"}]},
            },
            {"name": "httpx", "version": "1", "dependencies": [{"name": "httpcore"}]},
            {"name": "httpcore", "version": "1"},
            {"name": "pytest", "version": "1"},
            {"name": "playground", "version": "1"},
        ]
    }
    selected = dependency_closure(lock, "boxie")
    assert {item["name"] for item in selected} == {"boxie", "httpx", "httpcore"}
