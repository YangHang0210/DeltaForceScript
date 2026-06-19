# -*- coding: utf-8 -*-
# @Author: BugNotFound
# @Date: 2025-10-04
# @FilePath: /DeltaForceScript/main_gui.py
# @Description: 带 PyQt6 GUI 的主程序

import os
import sys
import re
import time
import ctypes
import logging
from datetime import datetime

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(
            os.path.join(_log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from window_capture import *
from region_selector import RegionSelector
from gui_monitor import MonitorWindow

import cv2
import numpy
from paddleocr import PaddleOCR
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
import pydirectinput
from colormath.color_objects import sRGBColor, LabColor
from colormath.color_diff import delta_e_cie2000
from colormath.color_conversions import convert_color

def patch_asscalar(a):
    return a.item()

setattr(numpy, "asscalar", patch_asscalar)

def is_admin():
    """检查是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """以管理员权限重新启动程序"""
    if not is_admin():
        logger.info("正在请求管理员权限...")
        # 获取当前脚本路径
        script = os.path.abspath(sys.argv[0])
        params = ' '.join([script] + sys.argv[1:])
        
        # 使用 ShellExecute 以管理员权限运行
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        
        if ret > 32:  # 成功
            sys.exit(0)
        else:
            logger.warning("未获得管理员权限，继续以普通权限运行")
            return False
    return True


def click_region_center(region: tuple, clicks=1, interval=0.1):
    """点击区域的中心位置 - 使用多种方法尝试
    
    Args:
        region: (left, top, right, bottom) 格式的区域坐标
    """
    left, top, right, bottom = region
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    
    # print(f"准备点击位置: ({center_x}, {center_y})")
    # 在20个像素的范围内随机偏移，防止被检测
    center_x += int((os.urandom(1)[0] / 255 - 0.5) * 10)
    center_y += int((os.urandom(1)[0] / 255 - 0.5) * 10)

    pydirectinput.click(x=center_x, y=center_y, clicks=clicks, interval=interval, button=pydirectinput.LEFT)

def extract_and_merge_digits(s: str) -> str:
    """识别字符串中的所有数字并合并为一个新字符串"""
    return ''.join(re.findall(r'\d', s))
    

class ScriptThread(QThread):
    """脚本运行线程"""
    
    status_updated = pyqtSignal(str)
    timer_updated = pyqtSignal(str, str)
    ocr_updated = pyqtSignal(str, float)
    click_performed = pyqtSignal()
    task_completed = pyqtSignal()
    
    def __init__(self, selector: RegionSelector, win_cap: WindowCapture, ocr, config):
        super().__init__()
        self.selector = selector
        self.win_cap = win_cap
        self.ocr = ocr
        self.config = config
        self.is_running = True
        self.is_paused = False
    
    def frame_cut(self, frame, region):
        """裁剪图像区域"""
        left, top, right, bottom = region
        return frame[top:bottom, left:right]

    def verify_window(self) -> bool:
        """检查确认按钮区域的颜色是否变化"""
        frame = self.win_cap.capture()
        while frame is None or frame.size == 0:
            time.sleep(0.05)
            frame = self.win_cap.capture()
        region = self.selector.get_region("verify_check")
        # 获取区域中心颜色
        color_tmp = frame[((region[1] + region[3]) // 2), ((region[0] + region[2]) // 2)]
        center_color = convert_color(
            sRGBColor(color_tmp[2], color_tmp[1], color_tmp[0]),  # BGR to sRGB
            LabColor
        )
        # 预设的确认按钮中心颜色 (BGR)
        target_color = convert_color(
            sRGBColor(175, 109, 65),  # BGR：适用于金色砖皮
            LabColor
        )
        # 计算颜色差异
        delta_e = delta_e_cie2000(center_color, target_color)
        # 色差小说明显示了确认窗口
        self.status_updated.emit(f"颜色：{color_tmp[2], color_tmp[1], color_tmp[0]}")
        self.status_updated.emit(f"色差: {delta_e}")
        if delta_e < 80:
            return True
        return False

    def ocr_region(self, region):
        """OCR 识别"""
        frame = self.win_cap.capture()
        # while frame is None or frame.size == 0: frame = self.win_cap.capture()
        if frame is None or frame.size == 0: return ""
        roi = self.frame_cut(frame, region)
        res = self.ocr.ocr(roi)
        if not res or not res[0]['rec_texts']:
            return ""
        return res[0]['rec_texts'][0]

    def run(self):
        """运行脚本"""
        try:
            self.status_updated.emit("初始化中...")
            
            time_region = self.selector.get_region("time")
            buy_region = self.selector.get_region("buy")
            verify_region = self.selector.get_region("verify")
            refresh_region = self.selector.get_region("refresh")
            money_region = self.selector.get_region("money")

            money = self.ocr_region(money_region)
            money = extract_and_merge_digits(money)
            self.status_updated.emit(f"初始三角币: {money}")
            pattern = re.compile(r'(\d+)\s*分\s*(\d+)\s*秒')
            
            self.status_updated.emit("监控中...")
            refreshed = False  # 标记是否刚刚点击过刷新
            click_region_center(refresh_region)
            while self.is_running:
                # 暂停时等待
                while self.is_paused: time.sleep(0.2); continue
                # 截图并OCR识别时间
                res = self.ocr_region(time_region)
                if "天" in res or "小时" in res:
                    click_region_center(refresh_region)
                    time.sleep(self.config['ocr_interval'])
                    continue
                match = pattern.search(res)
                if match:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    # 更新时间显示
                    self.timer_updated.emit(str(minutes), str(seconds))
                    # 剩余时间到 0:03 时点击刷新（如果启用）
                    if minutes == 0 and seconds == 3 and self.config['click_refresh_at_3s'] and not refreshed:
                        self.status_updated.emit("🔄 点击刷新...")
                        click_region_center(refresh_region)
                        refreshed = True
                    # 剩余时间到 0:01 时执行点击
                    if minutes == 0 and seconds == 1:
                        self.status_updated.emit("准备点击...")
                        time.sleep(self.config['buy_click_delay'])
                        # 点击购买按钮
                        click_region_center(buy_region, interval=0)
                        # 校验点击是否成功（可能造成延迟）
                        buy_count = 0
                        while not self.verify_window() and buy_count < 5:
                            buy_count += 1
                            if buy_count <= 2:
                                time.sleep(self.config['buy_interval'])
                                click_region_center(buy_region, interval=0)
                        time.sleep(self.config['buy_to_verify_delay'])
                        # 点击确认按钮
                        click_region_center(verify_region, interval=self.config['verify_interval'])
                        self.status_updated.emit("点击确认按钮...")
                        # 校验点到了确认
                        verify_counter = 0
                        while self.verify_window():
                            verify_counter += 1
                            if verify_counter > 2:
                                pydirectinput.click(1, 1, interval=0.1)
                            click_region_center(verify_region, interval=self.config['verify_interval'])
                        
                        self.status_updated.emit("等待刷新...")
                        time.sleep(1.5)
                        if self.verify_window(): pydirectinput.press('esc')
                        click_region_center(refresh_region)
                        # 检查三角币是否变化
                        now_money = self.ocr_region(money_region)
                        now_money = extract_and_merge_digits(now_money)
                        self.status_updated.emit(f"当前三角币: {now_money}")
                        self.config['continue_after_complete'] &= (now_money == money)
                        # 根据配置决定是否继续
                        if not self.config['continue_after_complete']:
                            self.status_updated.emit("任务完成！")
                            self.task_completed.emit()
                            break
                        else:
                            refreshed = False
                            self.status_updated.emit("继续监控中...")
                    else:
                        if minutes > 0 or seconds > 5:
                            time.sleep(self.config['ocr_interval'])
                else:
                    time.sleep(self.config['ocr_interval'])
        except Exception as e:
            self.status_updated.emit(f"错误: {str(e)}")
            logger.error("脚本运行错误", exc_info=True)
    
    def pause(self):
        self.is_paused = True
    
    def resume(self):
        self.is_paused = False
    
    def stop(self):
        self.is_running = False


def main():
    """主函数"""
    app = QApplication(sys.argv)
    selector = RegionSelector()
    selector.load_regions_from_file("regions_2k.json")
    win_cap = WindowCapture(max_buffer_len=2)
    
    # 初始化 OCR
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_dir="models/PP-OCRv5_server_det_infer",
        text_recognition_model_dir="models/PP-OCRv5_server_rec_infer",
        # use_tensorrt=True,
        device='gpu:0'
    )
    window = MonitorWindow()
    window.show()
    # 移动到屏幕右下角
    screen = app.primaryScreen().geometry()
    win_h = window.height()
    x = screen.x() + 10
    y = screen.y() + screen.height() - win_h - 30
    window.move(x, y)
    window.add_log("程序已启动")
    window.add_log("点击 [开始] 按钮启动监控")
    script_thread = None
    
    def on_start():
        nonlocal script_thread
        window.add_log("正在启动监控线程...")
        
        # 获取当前配置
        config = window.get_config()
        window.add_log(f"配置: 购买延迟={config['buy_click_delay']}秒")
        
        script_thread = ScriptThread(selector, win_cap, ocr, config)
        
        script_thread.status_updated.connect(lambda s: window.update_status(s))
        script_thread.status_updated.connect(lambda s: window.add_log(s))
        script_thread.timer_updated.connect(lambda m, s: window.update_timer(m, s))
        script_thread.task_completed.connect(lambda: window.on_complete())
        
        script_thread.start()
    
    def on_pause():
        if script_thread:
            script_thread.pause()
    
    def on_resume():
        if script_thread:
            script_thread.resume()
    
    def on_stop():
        if script_thread:
            script_thread.stop()
            script_thread.wait()
    
    def on_preview():
        frame = win_cap.capture()
        if frame is None or frame.size == 0:
            window.add_log("截图失败，无法预览")
            return
        preview = frame.copy()
        colors = {
            "time": (0, 255, 255),
            "buy": (0, 255, 0),
            "verify": (0, 165, 255),
            "refresh": (255, 0, 0),
            "money": (255, 0, 255),
            "verify_check": (0, 255, 255),
        }
        for name, region in selector.get_all_regions().items():
            left, top, right, bottom = region
            color = colors.get(name, (255, 255, 255))
            cv2.rectangle(preview, (left, top), (right, bottom), color, 2)
            cv2.putText(preview, name, (left, top - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            if name == "verify_check":
                cx, cy = (left + right) // 2, (top + bottom) // 2
                actual_bgr = frame[cy, cx]
                swatch_x = right + 10
                target_bgr = (65, 109, 175)
                cv2.rectangle(preview, (swatch_x, top), (swatch_x + 60, top + 30), target_bgr, -1)
                cv2.putText(preview, "target", (swatch_x, top - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.rectangle(preview, (swatch_x, top + 35), (swatch_x + 60, top + 65),
                              (int(actual_bgr[0]), int(actual_bgr[1]), int(actual_bgr[2])), -1)
                cv2.putText(preview, "actual", (swatch_x, top + 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        window.show_preview(QPixmap.fromImage(qimg))

    def on_redraw(name):
        all_names = ["time", "buy", "verify", "refresh", "money", "verify_check"]
        targets = all_names if name == "__all__" else [name]
        win_cap.stop()
        del win_cap.camera
        window.showMinimized()
        app.processEvents()
        time.sleep(0.3)
        for region_name in targets:
            try:
                selector.select_region(region_name)
                window.add_log(f"✓ 区域 '{region_name}' 已更新: {selector.get_region(region_name)}")
            except ValueError:
                window.add_log(f"✗ 跳过区域 '{region_name}'")
        selector.save_regions_to_file("regions_2k.json")
        import dxcam
        win_cap.camera = dxcam.create(
            device_idx=win_cap.device_idx,
            output_idx=win_cap.output_idx,
            output_color="BGR",
            max_buffer_len=2,
        )
        window.showNormal()
        window.activateWindow()
        window.add_log("✏ 区域配置已保存到 regions_2k.json")

    window.controller.start_requested.connect(on_start)
    window.controller.pause_requested.connect(on_pause)
    window.controller.resume_requested.connect(on_resume)
    window.controller.stop_requested.connect(on_stop)
    window.controller.preview_requested.connect(on_preview)
    window.controller.redraw_requested.connect(on_redraw)
    
    def cleanup():
        if script_thread and script_thread.isRunning():
            script_thread.stop()
            script_thread.wait()
        win_cap.stop()
    
    app.aboutToQuit.connect(cleanup)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    # 检查并请求管理员权限
    if not is_admin():
        logger.info("检测到程序未以管理员权限运行")
        run_as_admin()
    else:
        logger.info("Delta Force 自动购买脚本 - PyQt6 GUI版本 (管理员模式)")
        main()
