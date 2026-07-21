"""UI 모듈 - Rich를 사용한 터미널 UI"""
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.align import Align
from datetime import datetime
from typing import Optional
import time


class AppUI:
    """애플리케이션 UI 클래스"""
    
    def __init__(self):
        self.console = Console()
        self.layout = Layout()
        self.status_text = "대기 중..."
        self.last_transcription = ""
        self.message_count = 0
        self.start_time = time.time()
        self.is_recording = False
        self.user_count = 0
        
        self._setup_layout()
    
    def _setup_layout(self):
        """레이아웃 설정"""
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=2),
            Layout(name="footer", size=7)
        )
        
        self.layout["main"].split_row(
            Layout(name="status"),
            Layout(name="transcription")
        )
    
    def _render_header(self) -> Panel:
        """헤더 렌더링"""
        title = Text("🎤 OBS Whisper - 실시간 음성 인식", style="bold cyan")
        return Panel(Align.center(title), border_style="cyan")
    
    def _render_status(self) -> Panel:
        """상태 패널 렌더링"""
        status_icon = "🔴" if self.is_recording else "⚪"
        status_line = f"{status_icon} 상태: {self.status_text}"
        
        elapsed = int(time.time() - self.start_time)
        time_str = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        
        content = f"{status_line}\n\n⏱️  실행 시간: {time_str}\n📨 전송된 메시지: {self.message_count}개\n👥 등록된 사용자: {self.user_count}명"
        
        return Panel(
            content,
            title="[bold]상태[/bold]",
            border_style="green" if self.is_recording else "yellow"
        )
    
    def _render_transcription(self) -> Panel:
        """전사 결과 패널 렌더링"""
        if not self.last_transcription:
            content = "[dim]인식된 텍스트가 없습니다...[/dim]"
        else:
            content = f"[bold cyan]{self.last_transcription}[/bold cyan]"
        
        return Panel(
            content,
            title="[bold]최근 인식 텍스트[/bold]",
            border_style="blue"
        )
    
    def _render_footer(self) -> Panel:
        """푸터 렌더링"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer_text = f"⏰ {now} | [dim]Ctrl+C로 종료[/dim]"
        return Panel(Align.center(footer_text), border_style="dim")
    
    def render(self) -> Layout:
        """전체 레이아웃 렌더링"""
        self.layout["header"].update(self._render_header())
        self.layout["status"].update(self._render_status())
        self.layout["transcription"].update(self._render_transcription())
        self.layout["footer"].update(self._render_footer())
        return self.layout
    
    def update_status(self, status: str):
        """상태 업데이트"""
        self.status_text = status
    
    def update_transcription(self, text: str, sent: bool = False):
        """전사 결과 업데이트"""
        self.last_transcription = text
        if sent:
            self.message_count += 1
    
    def set_recording(self, is_recording: bool):
        """녹음 상태 설정"""
        self.is_recording = is_recording
    
    def update_user_count(self, count: int):
        """등록된 사용자 수 업데이트"""
        self.user_count = count
    
    def show_startup(self):
        """시작 화면 표시"""
        self.console.clear()
        welcome = Panel(
            Align.center(
                Text("OBS Whisper 앱을 시작합니다...\n\n음성 입력을 기다리는 중...", style="bold")
            ),
            border_style="cyan",
            title="[bold cyan]시작 중[/bold cyan]"
        )
        self.console.print(welcome)
        time.sleep(1)
    
    def show_error(self, error: str):
        """에러 메시지 표시"""
        error_panel = Panel(
            f"[bold red]오류:[/bold red] {error}",
            border_style="red",
            title="[bold red]오류[/bold red]"
        )
        self.console.print(error_panel)

