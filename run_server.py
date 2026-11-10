"""Detached dev-server launcher (Windows-friendly)."""
from mymoney import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8602, debug=False)
