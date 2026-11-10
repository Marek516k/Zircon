import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "zircon_server.app:create_app",
        factory=True,
        host=os.environ.get("ZIRCON_HOST", "0.0.0.0"),
        port=int(os.environ.get("ZIRCON_PORT", "8000")),
    )
