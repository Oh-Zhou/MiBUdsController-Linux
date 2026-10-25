"""小米耳机 SAFER+ 128 位加密变体（移植自原项目 SaferPlus.cs）。"""

_PATTERN = 0x9999
_SEQUENCE = bytes([
    0x11, 0x22, 0x33, 0x33, 0x22, 0x11, 0x11, 0x22,
    0x33, 0x33, 0x22, 0x11, 0x11, 0x22, 0x33, 0x33,
])
_COEFFS = [
    [2, 1, 1, 1, 4, 2, 1, 1, 2, 2, 4, 2, 4, 4, 16, 8],
    [2, 1, 1, 1, 4, 2, 1, 1, 1, 1, 2, 1, 2, 2, 8, 4],
    [1, 1, 4, 2, 2, 2, 4, 2, 16, 8, 4, 4, 2, 1, 1, 1],
    [1, 1, 4, 2, 1, 1, 2, 1, 8, 4, 2, 2, 2, 1, 1, 1],
    [16, 8, 2, 2, 4, 2, 4, 4, 1, 1, 4, 2, 1, 1, 2, 1],
    [8, 4, 1, 1, 2, 1, 2, 2, 1, 1, 4, 2, 1, 1, 2, 1],
    [2, 2, 4, 2, 4, 4, 16, 8, 2, 1, 1, 1, 4, 2, 1, 1],
    [1, 1, 2, 1, 2, 2, 8, 4, 2, 1, 1, 1, 4, 2, 1, 1],
    [4, 2, 4, 4, 16, 8, 2, 2, 1, 1, 2, 1, 1, 1, 4, 2],
    [2, 1, 2, 2, 8, 4, 1, 1, 1, 1, 2, 1, 1, 1, 4, 2],
    [4, 4, 16, 8, 1, 1, 2, 1, 4, 2, 1, 1, 4, 2, 2, 2],
    [2, 2, 8, 4, 1, 1, 2, 1, 4, 2, 1, 1, 2, 1, 1, 1],
    [1, 1, 2, 1, 1, 1, 4, 2, 4, 4, 16, 8, 2, 2, 4, 2],
    [1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 8, 4, 1, 1, 2, 1],
    [4, 2, 1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 16, 8, 4, 4],
    [4, 2, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 8, 4, 2, 2],
]


def _modpow(base: int, exp: int, mod: int) -> int:
    result = 1
    base %= mod
    while exp:
        if exp & 1:
            result = (result * base) % mod
        base = (base * base) % mod
        exp >>= 1
    return result


def _bit_set(index: int) -> bool:
    return ((1 << index) & _PATTERN) != 0


class SaferPlus:
    """SAFER+ 变体：128 位密钥、8 轮加密，用于耳机鉴权挑战响应。"""

    def __init__(self) -> None:
        self._bias = self._make_bias()
        self._exp = self._make_exp()
        self._log = self._make_log()

    @staticmethod
    def _make_bias() -> list[list[int]]:
        table = [[0] * 16 for _ in range(16)]
        for i in range(16):
            for j in range(16):
                exponent = 17 * (i + 2) + (j + 1)
                inner = _modpow(45, exponent, 257)
                value = _modpow(45, inner, 257)
                table[i][j] = 0 if value == 256 else value
        return table

    @staticmethod
    def _make_exp() -> list[int]:
        table = [0] * 256
        for i in range(256):
            value = _modpow(45, i, 257)
            table[i] = 0 if i == 128 else (value & 0xFF)
        return table

    @staticmethod
    def _make_log() -> list[int]:
        table = [0] * 256
        for i in range(256):
            if i == 0:
                table[i] = 128
            else:
                value = _modpow(45, i, 257)
                if value != 256:
                    table[value] = i
        return table

    @staticmethod
    def _rotl5(value: int) -> int:
        value &= 0xFF
        return ((value >> 5) | (value << 3)) & 0xFF

    def _key_schedule(self, key_init: list[int]) -> list[list[int]]:
        key = list(key_init)
        key[15] ^= 6
        keys = [key]
        register = key + [0]
        xor = 0
        for byte in key:
            xor ^= byte
        register[16] = xor
        for key_idx in range(1, 17):
            register = [self._rotl5(x) for x in register]
            round_key = [
                (register[(key_idx + i) % 17] + self._bias[key_idx - 1][i]) & 0xFF
                for i in range(16)
            ]
            keys.append(round_key)
        return keys

    def _encrypt(self, plaintext: list[int], keys: list[list[int]]) -> bytes:
        ct = list(plaintext)
        for rnd in range(8):
            if rnd == 2:
                for i in range(16):
                    if _bit_set(i):
                        ct[i] ^= plaintext[i]
                    else:
                        ct[i] = (ct[i] + plaintext[i]) & 0xFF
            for i in range(16):
                if _bit_set(i):
                    ct[i] ^= keys[rnd * 2][i]
                else:
                    ct[i] = (ct[i] + keys[rnd * 2][i]) & 0xFF
            for i in range(16):
                if _bit_set(i):
                    ct[i] = self._exp[ct[i]]
                else:
                    ct[i] = self._log[ct[i]]
            for i in range(16):
                if _bit_set(i):
                    ct[i] = (keys[rnd * 2 + 1][i] + ct[i]) & 0xFF
                else:
                    ct[i] = keys[rnd * 2 + 1][i] ^ ct[i]
            copy = ct[:]
            for i in range(16):
                s = 0
                for j in range(16):
                    s = (s + _COEFFS[i][j] * copy[j]) & 0xFF
                ct[i] = s
        for i in range(16):
            if _bit_set(i):
                ct[i] ^= keys[16][i]
            else:
                ct[i] = (ct[i] + keys[16][i]) & 0xFF
        return bytes(ct)

    @staticmethod
    def generate_challenge() -> bytes:
        import os
        return os.urandom(16)

    def compute_challenge_response(self, challenge: bytes) -> bytes:
        keys = self._key_schedule(list(challenge))
        return self._encrypt(list(_SEQUENCE), keys)
