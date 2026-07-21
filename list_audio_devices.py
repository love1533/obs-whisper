"""오디오 입력 장치 목록 확인 및 선택 스크립트"""
import pyaudio

def list_audio_devices():
    """사용 가능한 오디오 입력 장치 목록 출력"""
    p = pyaudio.PyAudio()
    
    print("=" * 70)
    print("사용 가능한 오디오 입력 장치:")
    print("=" * 70)
    
    input_devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            input_devices.append((i, info))
            default_mark = " [기본]" if i == p.get_default_input_device_info().get('index', -1) else ""
            print(f"  [{i:2d}] {info['name']}")
            print(f"       채널: {info['maxInputChannels']}, 샘플레이트: {int(info['defaultSampleRate'])}Hz{default_mark}")
            print()
    
    if not input_devices:
        print("  ⚠️  입력 장치를 찾을 수 없습니다.")
        print()
        print("  OBS 오디오를 받으려면:")
        print("  1. BlackHole 설치: https://existential.audio/blackhole/")
        print("  2. macOS 시스템 설정에서 마이크 권한 허용")
        print("  3. 오디오 MIDI 설정에서 멀티 출력 장치 생성")
    else:
        print("=" * 70)
        print("💡 BlackHole을 사용하려면:")
        print("   - 장치 목록에서 'BlackHole 2ch' 또는 'BlackHole'을 찾으세요")
        print("   - 해당 장치 번호를 .env 파일의 INPUT_DEVICE_INDEX에 설정하세요")
        print("=" * 70)
    
    p.terminate()
    return input_devices

if __name__ == "__main__":
    list_audio_devices()


