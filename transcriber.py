"""음성 인식 모듈

기본 백엔드는 MLX Whisper(Apple Silicon GPU, Metal 가속)이며, 필요 시
faster-whisper(CPU)로 전환할 수 있다. Mac Studio(M4 Max)에서 MLX large-v3는
실시간의 십수 배 속도로 동작한다.
"""
import sys
import numpy as np
from typing import Optional


class Transcriber:
    """MLX / faster-whisper 백엔드를 지원하는 음성 인식 클래스"""

    def __init__(
        self,
        backend: str = "mlx",
        model_size: str = "large-v3",
        mlx_repo: str = "mlx-community/whisper-large-v3-mlx",
        device: str = "auto",
        compute_type: str = "int8",
        language: str = "ko",
        beam_size: int = 5,
        initial_prompt: Optional[str] = None,
    ):
        self.backend = backend.lower()
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.mlx_repo = mlx_repo

        if self.backend == "mlx":
            self._init_mlx()
        else:
            self._init_faster_whisper(model_size, device, compute_type)

    # ------------------------------------------------------------------ MLX
    def _init_mlx(self):
        import mlx_whisper  # noqa
        self._mlx = mlx_whisper
        print(f"MLX Whisper 로딩 및 워밍업 중: {self.mlx_repo} (Apple GPU)...", file=sys.stderr)
        # 첫 호출은 Metal 커널 컴파일로 매우 느리므로, 시작 시 무음으로 워밍업해
        # 실제 첫 발화에서 지연이 없도록 한다.
        try:
            self._mlx.transcribe(
                np.zeros(16000, dtype=np.float32),
                path_or_hf_repo=self.mlx_repo,
                language=self.language,
            )
        except Exception as e:
            print(f"⚠️  MLX 워밍업 경고: {e}", file=sys.stderr)
        print("MLX Whisper 준비 완료!", file=sys.stderr)

    def _transcribe_mlx(self, audio_data: np.ndarray) -> Optional[str]:
        result = self._mlx.transcribe(
            audio_data,
            path_or_hf_repo=self.mlx_repo,
            language=self.language,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
        )
        return (result.get("text") or "").strip()

    # -------------------------------------------------------- faster-whisper
    def _init_faster_whisper(self, model_size, device, compute_type):
        from faster_whisper import WhisperModel
        print(f"faster-whisper 모델 로딩 중: {model_size} ({device}/{compute_type})...", file=sys.stderr)
        self._fw = WhisperModel(model_size, device=device, compute_type=compute_type)
        print("모델 로딩 완료!", file=sys.stderr)

    def _transcribe_fw(self, audio_data: np.ndarray) -> Optional[str]:
        segments, _info = self._fw.transcribe(
            audio_data,
            language=self.language,
            task="transcribe",
            beam_size=self.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    # ---------------------------------------------------------------- 공통 API
    def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> Optional[str]:
        """
        오디오 데이터를 텍스트로 변환

        Args:
            audio_data: float32 오디오 (-1.0 ~ 1.0), 16kHz 모노
            sample_rate: 샘플링 레이트 (참고용, 오디오는 16kHz 전제)

        Returns:
            전사된 텍스트 또는 None
        """
        if audio_data is None or len(audio_data) == 0:
            return None

        try:
            if self.backend == "mlx":
                text = self._transcribe_mlx(audio_data)
            else:
                text = self._transcribe_fw(audio_data)

            if not text or len(text) < 2:
                return None
            return text

        except Exception as e:
            print(f"      ❌ 전사 오류: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None
