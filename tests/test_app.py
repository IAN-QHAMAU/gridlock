"""End-to-end smoke tests driving the real Streamlit app.

These run the script exactly as Streamlit does, so they catch broken widget
wiring, session-state conflicts and rendering errors that unit tests miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the app at a throwaway SQLite database."""
    monkeypatch.setenv("TTT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FORCE_LOCAL_BACKEND", "true")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    import config
    import database

    config.get_settings(refresh=True)
    database.get_database(refresh=True)


def run_app(timeout: int = 30) -> "AppTest":
    """Start the app and return the finished test harness."""
    app = AppTest.from_file(APP, default_timeout=timeout)
    app.run()
    return app


def goto(app: "AppTest", page: str) -> "AppTest":
    """Navigate using the sidebar."""
    app.sidebar.radio[0].set_value(page).run()
    return app


def test_landing_page_renders() -> None:
    app = run_app()
    assert not app.exception
    assert any("GRIDLOCK" in str(block.value) for block in app.markdown)


@pytest.mark.parametrize(
    "page",
    ["Play", "Online", "Statistics", "Leaderboard", "Achievements", "Daily challenge",
     "Replays", "Settings"],
)
def test_every_page_renders(page: str) -> None:
    app = goto(run_app(), page)
    assert not app.exception, f"{page} raised {app.exception}"


def test_playing_a_move_marks_the_board() -> None:
    app = goto(run_app(60), "Play")
    board_buttons = [button for button in app.button if button.key and button.key.startswith("cell-")]
    assert len(board_buttons) == 9

    board_buttons[0].click().run()
    assert not app.exception
    game = app.session_state["game"]
    assert game.board.cells[0] == "X"
    # The engine answers immediately, so the board holds two marks.
    assert game.board.move_count == 2


def test_theme_switch_applies() -> None:
    app = run_app()
    app.sidebar.selectbox[0].set_value("light").run()
    assert not app.exception
    assert app.session_state["prefs"]["theme"] == "light"


def test_create_room_shows_a_code() -> None:
    app = goto(run_app(60), "Online")
    create = next(button for button in app.button if button.key == "create_room")
    create.click().run()
    assert not app.exception
    code = app.session_state["room_code"]
    assert code and len(code) == 5


def test_settings_profile_update() -> None:
    app = goto(run_app(), "Settings")
    app.text_input[0].set_value("Ada Lovelace").run()
    next(button for button in app.button if button.key == "settings_save").click().run()
    assert not app.exception
    assert app.session_state["identity"]["name"] == "Ada Lovelace"


def seated_client(player_id: str, name: str, avatar: str, code: str) -> "AppTest":
    """An app already signed in as `player_id` and sitting in room `code`."""
    app = AppTest.from_file(APP, default_timeout=60)
    app.session_state["identity"] = {
        "player_id": player_id,
        "name": name,
        "avatar": avatar,
        "is_guest": True,
        "email": "",
        "provider": "guest",
    }
    app.session_state["current_page"] = "Online"
    app.session_state["room_code"] = code
    app.run()
    return app


def cells(app: "AppTest") -> list:
    """Board buttons in index order."""
    return sorted(
        (button for button in app.button if button.key and button.key.startswith("cell-")),
        key=lambda button: int(button.key.split("-")[1]),
    )


def test_two_clients_play_an_online_match() -> None:
    """Host and guest run as separate sessions against the same backend."""
    from database import get_database
    from multiplayer import RoomManager

    rooms = RoomManager(get_database())
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    code = state.code

    host = seated_client("host-1", "Ada", "🦊", code)
    assert not host.exception
    assert not cells(host)[0].disabled  # X moves first
    cells(host)[0].click().run()
    assert not host.exception
    assert rooms.fetch(code).board.cells[0] == "X"

    guest = seated_client("guest-1", "Linus", "🐙", code)
    assert not guest.exception
    assert cells(guest)[0].disabled  # already taken by the host
    cells(guest)[4].click().run()
    assert not guest.exception

    final = rooms.fetch(code)
    assert final.board.cells[4] == "O"
    assert final.move_count == 2
    assert final.current_turn.value == "X"

    # With the turn back on the host, the guest's board is frozen.
    guest.run()
    assert all(button.disabled for button in cells(guest))


def test_spectator_cannot_move() -> None:
    """A third session sees the game but every cell is locked."""
    from database import get_database
    from multiplayer import RoomManager

    rooms = RoomManager(get_database())
    state = rooms.create_room("host-2", "Ada", "🦊")
    rooms.join_room(state.code, "guest-2", "Linus", "🐙")

    watcher = seated_client("watcher-1", "Grace", "🦉", state.code)
    assert not watcher.exception
    assert all(button.disabled for button in cells(watcher))
