"""PyTorch から Radeon GPU が使えるかを確認する。

使い方:  source scripts/env.sh && python scripts/check_gpu.py
"""

import sys
import time


def main() -> int:
    try:
        import torch
    except ImportError:
        print("NG: torch が入っていません。scripts/02_install_sbv2.sh を実行してください。")
        return 1

    print(f"torch        : {torch.__version__}")
    print(f"HIP (ROCm)   : {torch.version.hip}")
    if not torch.version.hip:
        print("NG: ROCm 版ではない torch です（CUDA 版または CPU 版）。")
        return 1

    if not torch.cuda.is_available():
        print("NG: GPU が見えません。docs/troubleshooting.md の「GPU が認識されない」を参照。")
        return 1

    dev = torch.device("cuda")  # ROCm でも API 名は cuda
    props = torch.cuda.get_device_properties(dev)
    arch = getattr(props, "gcnArchName", "?")
    print(f"GPU          : {props.name} ({arch})")
    print(f"VRAM         : {props.total_memory / 1024**3:.1f} GiB")

    try:
        # 音声モデルで多用する演算をひと通り試す
        x = torch.randn(4, 192, 8192, device=dev)
        conv = torch.nn.Conv1d(192, 192, 5, padding=2).to(dev)
        y = conv(x)
        wav = torch.randn(4, 44100, device=dev)
        spec = torch.stft(
            wav, n_fft=2048, hop_length=512, window=torch.hann_window(2048, device=dev),
            return_complex=True,
        )
        a = torch.randn(2048, 2048, device=dev)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(20):
            a = a @ a / 2048**0.5
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        (y.mean() + spec.abs().mean()).backward()  # 逆伝播も確認
        torch.cuda.synchronize()
        print(f"演算テスト   : OK (conv1d / stft / matmul, matmul x20 = {dt * 1000:.0f} ms)")
    except Exception as e:  # noqa: BLE001
        print(f"NG: GPU 上の演算で失敗しました: {e!r}")
        return 1

    print("\nOK: 学習に使えます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
