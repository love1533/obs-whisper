"""실시간 오디오 녹음 모듈"""
import pyaudio
import numpy as np
from typing import Callable, Optional
import threading
import queue


class AudioRecorder:
    """실시간 오디오 녹음 클래스"""
    
    def __init__(
        self,
        chunk_size: int = 1024,
        sample_rate: int = 16000,
        channels: int = 1,
        format: str = "int16",
        input_device_index: Optional[int] = None
    ):
        self.chunk_size = chunk_size
        self.sample_rate = sample_rate
        self.channels = channels
        self.format = format
        self.input_device_index = input_device_index
        self.audio_queue = queue.Queue()
        self.is_recording = False
        self.audio_thread: Optional[threading.Thread] = None
        self.pyaudio_instance: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        self.actual_channels = channels  # 실제 사용 중인 채널 수 (기본값)
        
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """오디오 입력 콜백"""
        if status:
            print(f"오디오 상태: {status}")
        self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)
    
    def _record_audio(self):
        """오디오 녹음 스레드"""
        try:
            self.pyaudio_instance = pyaudio.PyAudio()
            
            # 입력 장치 선택
            input_device_index = self.input_device_index
            if input_device_index is None:
                # 기본 입력 장치 사용
                try:
                    input_device_index = self.pyaudio_instance.get_default_input_device_info()['index']
                except:
                    # 기본 장치가 없으면 첫 번째 입력 장치 찾기
                    for i in range(self.pyaudio_instance.get_device_count()):
                        device_info = self.pyaudio_instance.get_device_info_by_index(i)
                        if device_info['maxInputChannels'] > 0:
                            input_device_index = i
                            break
                    if input_device_index is None:
                        raise Exception("사용 가능한 입력 장치를 찾을 수 없습니다.")
            
            # 장치 정보 확인 및 채널 수 자동 감지
            device_info = self.pyaudio_instance.get_device_info_by_index(input_device_index)
            device_max_channels = device_info['maxInputChannels']
            
            # 장치가 지원하는 채널 수 사용 (BlackHole 2ch는 2채널이므로 2채널로 받음)
            # 나중에 모노로 변환하므로 장치가 지원하는 채널 수를 그대로 사용
            if device_max_channels > 0:
                actual_channels = device_max_channels
            else:
                actual_channels = self.channels
            
            # 오디오 포맷 설정
            if self.format == "int16":
                audio_format = pyaudio.paInt16
            else:
                audio_format = pyaudio.paInt16
            
            print(f"🎤 오디오 입력 장치 사용: 인덱스 {input_device_index} ({device_info['name']})")
            print(f"   장치 지원 채널: {device_max_channels}, 사용 채널: {actual_channels}")
            
            self.stream = self.pyaudio_instance.open(
                format=audio_format,
                channels=actual_channels,  # 장치가 지원하는 채널 수 사용
                rate=self.sample_rate,
                input=True,
                input_device_index=input_device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback
            )
            
            # 실제 사용 중인 채널 수 저장 (나중에 모노 변환에 사용)
            self.actual_channels = actual_channels
            
            self.stream.start_stream()
            
            # 스트림이 활성화되어 있는 동안 대기
            while self.is_recording and self.stream.is_active():
                import time
                time.sleep(0.1)
                
        except Exception as e:
            print(f"오디오 녹음 오류: {e}")
        finally:
            self._cleanup()
    
    def _cleanup(self):
        """리소스 정리"""
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
        
        if self.pyaudio_instance:
            try:
                self.pyaudio_instance.terminate()
            except:
                pass
    
    def start(self):
        """녹음 시작"""
        if self.is_recording:
            return
        
        self.is_recording = True
        self.audio_thread = threading.Thread(target=self._record_audio, daemon=True)
        self.audio_thread.start()
        print("오디오 녹음이 시작되었습니다.")
    
    def stop(self):
        """녹음 중지"""
        self.is_recording = False
        if self.audio_thread:
            self.audio_thread.join(timeout=2.0)
        print("오디오 녹음이 중지되었습니다.")
    
    def get_audio_chunk(self, timeout: float = 1.0) -> Optional[bytes]:
        """오디오 청크 가져오기"""
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_audio_data(self, duration_seconds: float) -> Optional[np.ndarray]:
        """지정된 시간만큼 오디오 데이터 수집"""
        import sys
        
        chunks_needed = int((self.sample_rate / self.chunk_size) * duration_seconds)
        audio_data = []
        
        for i in range(chunks_needed):
            chunk = self.get_audio_chunk(timeout=2.0)
            if chunk is None:
                print(f"      ⚠️  청크 {i+1}/{chunks_needed} 수집 실패 (타임아웃)", file=sys.stderr)
                return None
            audio_data.append(chunk)
        
        if not audio_data:
            print(f"      ❌ 수집된 오디오 데이터가 없습니다.", file=sys.stderr)
            return None
        
        # 바이트 데이터를 numpy 배열로 변환
        audio_array = np.frombuffer(b''.join(audio_data), dtype=np.int16)
        
        # 실제 사용 중인 채널 수 확인 (기본값은 self.channels)
        actual_channels = getattr(self, 'actual_channels', self.channels)
        
        # 2채널인 경우 모노로 변환 (평균 사용)
        if actual_channels == 2:
            # 2채널 데이터를 reshape하고 평균을 내어 모노로 변환
            audio_array = audio_array.reshape(-1, 2).mean(axis=1)
        
        # float32로 정규화 (-1.0 ~ 1.0)
        audio_array = audio_array.astype(np.float32) / 32768.0
        
        return audio_array

