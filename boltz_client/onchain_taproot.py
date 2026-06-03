import json
from dataclasses import dataclass
from typing import Any


@dataclass
class TaprootSwapData:
    swap_tree: dict[str, Any]
    server_public_key: str

    @classmethod
    def from_json(cls, raw: str) -> "TaprootSwapData":
        data = json.loads(raw)
        return cls(
            swap_tree=data["swapTree"],
            server_public_key=data["serverPublicKey"],
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "swapTree": self.swap_tree,
                "serverPublicKey": self.server_public_key,
            },
            separators=(",", ":"),
        )


def taproot_swap_data_from_response(
    swap_tree: dict[str, Any] | None,
    server_public_key: str | None,
) -> str:
    if not swap_tree or not server_public_key:
        return ""
    return TaprootSwapData(swap_tree, server_public_key).to_json()


def is_taproot_swap_data(raw: str) -> bool:
    try:
        TaprootSwapData.from_json(raw)
        return True
    except (json.JSONDecodeError, KeyError, TypeError):
        return False
