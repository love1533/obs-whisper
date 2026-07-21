"""VAD 기반 발화 분절 모듈

Silero VAD로 프레임 단위 음성 확률을 계산하고, 말이 끊기는(정적) 지점을
감지해 발화(utterance) 단위로 오디오를 잘라 준다. 고정 5초 청크와 달리
사람이 쉬는 지점에서 끊기므로 문장이 중간에 잘리지 않는다.
"""
import sys
import numpy as np
from collections import deque
from typing import Optional

import torch
from silero_vad import load_silero_vad


class VADSegmenter:
    """Silero VAD를 이용한 실시간 발화 분절기"""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_samples: int = 512,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        silence_ms: int = 700,
        max_utterance_s: float = 15.0,
    ):
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.frame_ms = frame_samples / sample_rate * 1000.0
        self.threshold = threshold

        # ms → 프레임 수 변환
        self.min_speech_frames = max(1, round(min_speech_ms / self.frame_ms))
        self.silence_frames = max(1, round(silence_ms / self.frame_ms))
        self.max_utterance_frames = max(1, round(max_utterance_s * 1000 / self.frame_ms))
        self.preroll_frames = self.min_speech_frames  # 발화 시작 앞부분 손실 방지

        print("Silero VAD 모델 로딩 중...", file=sys.stderr)
        self.model = load_silero_vad()
        print("Silero VAD 로딩 완료!", file=sys.stderr)

        self.reset()

    def reset(self):
        """상태 초기화 (재접속 시 호출)"""
        self._triggered = False
        self._speech_run = 0
        self._silence_run = 0
        self._utterance = []               # 현재 발화 프레임 목록
        self._preroll = deque(maxlen=self.preroll_frames)
        try:
            self.model.reset_states()
        except Exception:
            pass

    def process_frame(self, frame_i16: np.ndarray) -> Optional[np.ndarray]:
        """프레임 하나를 처리. 발화가 끝나면 float32 오디오(-1~1) 반환, 아니면 None."""
        frame_f32 = frame_i16.astype(np.float32) / 32768.0

        with torch.no_grad():
            prob = self.model(torch.from_numpy(frame_f32), self.sample_rate).item()
        is_speech = prob >= self.threshold

        if not self._triggered:
            self._preroll.append(frame_f32)
            if is_speech:
                self._speech_run += 1
                if self._speech_run >= self.min_speech_frames:
                    # 발화 시작 - preroll을 포함해 버퍼 구성
                    self._triggered = True
                    self._utterance = list(self._preroll)
                    self._preroll.clear()
                    self._silence_run = 0
            else:
                self._speech_run = 0
            return None

        # 발화 진행 중
        self._utterance.append(frame_f32)

        if is_speech:
            self._silence_run = 0
        else:
            self._silence_run += 1

        end_by_silence = self._silence_run >= self.silence_frames
        end_by_length = len(self._utterance) >= self.max_utterance_frames

        if end_by_silence or end_by_length:
            return self._flush()

        return None

    def _flush(self) -> Optional[np.ndarray]:
        """현재 발화를 하나의 오디오 배열로 반환하고 상태 리셋"""
        if not self._utterance:
            self._triggered = False
            return None
        audio = np.concatenate(self._utterance).astype(np.float32)
        # 상태 리셋 (VAD 모델 상태는 연속 스트림이므로 유지)
        self._triggered = False
        self._speech_run = 0
        self._silence_run = 0
        self._utterance = []
        self._preroll.clear()
        return audio
