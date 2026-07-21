"""오디오 입력 테스트 스크립트"""
import pyaudio
import numpy as np
import time
import sys
from config import INPUT_DEVICE_INDEX, AUDIO_RATE, AUDIO_CHUNK_SIZE

def test_audio_input():
    """오디오 입력 장치 테스트"""
    print("=" * 70)
    print("오디오 입력 테스트")
    print("=" * 70)
    print()
    
    # 설정 확인
    print("📋 현재 설정:")
    print(f"   INPUT_DEVICE_INDEX: {INPUT_DEVICE_INDEX}")
    print(f"   AUDIO_RATE: {AUDIO_RATE} Hz")
    print(f"   AUDIO_CHUNK_SIZE: {AUDIO_CHUNK_SIZE}")
    print()
    
    p = pyaudio.PyAudio()
    
    # 입력 장치 선택
    input_device_index = INPUT_DEVICE_INDEX
    if input_device_index is None:
        try:
            input_device_index = p.get_default_input_device_info()['index']
            print(f"✅ 기본 입력 장치 사용: 인덱스 {input_device_index}")
        except:
            print("⚠️  기본 입력 장치를 찾을 수 없습니다. 사용 가능한 장치 검색 중...")
            for i in range(p.get_device_count()):
                device_info = p.get_device_info_by_index(i)
                if device_info['maxInputChannels'] > 0:
                    input_device_index = i
                    print(f"   발견: 인덱스 {i} - {device_info['name']}")
                    break
            if input_device_index is None:
                print("❌ 사용 가능한 입력 장치를 찾을 수 없습니다.")
                p.terminate()
                return
    
    # 장치 정보 확인
    device_info = p.get_device_info_by_index(input_device_index)
    device_max_channels = device_info['maxInputChannels']
    
    print()
    print("🎤 선택된 오디오 입력 장치:")
    print(f"   인덱스: {input_device_index}")
    print(f"   이름: {device_info['name']}")
    print(f"   최대 입력 채널: {device_max_channels}")
    print(f"   샘플레이트: {device_info['defaultSampleRate']} Hz")
    print()
    
    # 실제 사용할 채널 수 결정
    if device_max_channels > 0:
        actual_channels = device_max_channels
    else:
        actual_channels = 1
    
    print(f"📊 사용 설정:")
    print(f"   채널: {actual_channels}")
    print(f"   샘플레이트: {AUDIO_RATE} Hz")
    print()
    
    # 오디오 스트림 열기
    print("🔄 오디오 스트림 시작 중...")
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=actual_channels,
            rate=AUDIO_RATE,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=AUDIO_CHUNK_SIZE
        )
        
        print("✅ 오디오 스트림이 시작되었습니다.")
        print()
        print("=" * 70)
        print("오디오 신호 모니터링 (10초간, Ctrl+C로 중단)")
        print("=" * 70)
        print()
        
        # 10초간 오디오 신호 모니터링
        start_time = time.time()
        max_signal = 0
        min_signal = float('inf')
        samples_collected = 0
        
        try:
            while time.time() - start_time < 10:
                # 오디오 데이터 읽기
                data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                audio_array = np.frombuffer(data, dtype=np.int16)
                
                # 2채널인 경우 모노로 변환
                if actual_channels == 2:
                    audio_array = audio_array.reshape(-1, 2).mean(axis=1)
                
                # float32로 정규화
                audio_array_float = audio_array.astype(np.float32) / 32768.0
                
                # 신호 강도 계산
                max_val = abs(audio_array_float).max()
                rms = np.sqrt((audio_array_float ** 2).mean())
                
                if max_val > max_signal:
                    max_signal = max_val
                if max_val < min_signal:
                    min_signal = max_val
                
                samples_collected += len(audio_array_float)
                
                # 실시간 출력
                elapsed = time.time() - start_time
                status = "🔊" if max_val > 0.001 else "🔇"
                print(f"[{elapsed:5.1f}s] {status} 최대: {max_val:.6f}, RMS: {rms:.6f}, 샘플: {len(audio_array_float)}", end='\r')
                
                time.sleep(0.1)
        
        except KeyboardInterrupt:
            print("\n\n⚠️  사용자에 의해 중단되었습니다.")
        
        print()
        print()
        print("=" * 70)
        print("테스트 결과")
        print("=" * 70)
        print()
        print(f"📊 수집된 샘플 수: {samples_collected}")
        print(f"📈 최대 신호 강도: {max_signal:.6f}")
        print(f"📉 최소 신호 강도: {min_signal:.6f}")
        print()
        
        if max_signal < 0.001:
            print("❌ 경고: 오디오 신호가 매우 약합니다!")
            print("   가능한 원인:")
            print("   1. OBS에서 BlackHole로 오디오가 라우팅되지 않았습니다")
            print("   2. BlackHole 장치가 올바르게 설정되지 않았습니다")
            print("   3. 입력 장치 인덱스가 잘못되었습니다")
            print()
            print("   해결 방법:")
            print("   1. OBS 설정 > 오디오 > 모니터링 장치를 'BlackHole 2ch'로 설정")
            print("   2. OBS 오디오 소스의 '모니터링'을 '모니터링만' 또는 '모니터링 및 출력'으로 설정")
            print("   3. 'python3 list_audio_devices.py'로 올바른 장치 인덱스 확인")
        elif max_signal < 0.01:
            print("⚠️  오디오 신호가 약합니다. OBS 설정을 확인하세요.")
        else:
            print("✅ 오디오 신호가 정상적으로 감지되고 있습니다!")
        
        stream.stop_stream()
        stream.close()
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        p.terminate()
        print()
        print("=" * 70)

if __name__ == "__main__":
    test_audio_input()
