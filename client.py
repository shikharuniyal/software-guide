import os
import mss
import cv2
import numpy as np
import base64
import time
import requests
import uiautomation as auto
import sys
import ctypes
import hashlib

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PyQt5.QtCore import Qt, QEventLoop, QTimer
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

# Hardware Event Listeners
from pynput import mouse, keyboard

# Address of the server running the docker stack.
# On a real (air-gapped) server, set the SERVER_URL env var to the server's IP, e.g.
#   set SERVER_URL=http://10.0.0.5:8000/guide   (Windows)
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000/guide")
REQUEST_TIMEOUT = 180      
MAX_STEPS = 30             # to prevent loops

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # dpi sensetive scaling for win desktop to desktop
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)

app = QApplication.instance() or QApplication(sys.argv)


def ui_signature(elements):
    return "\n".join(f"- {e['name']} ({e['type']})" for e in elements if e["name"])[:2000]

with mss.MSS() as _sct: 
    _m = _sct.monitors[1]
    SCREEN_W, SCREEN_H = _m["width"], _m["height"]
    TOTAL_PIXELS = SCREEN_W * SCREEN_H

# screen and uia fetch
def capture_screen():
    with mss.MSS() as sct:  
        raw = sct.grab(sct.monitors[1])
    img = np.array(raw)[:, :, :3]
    ok, buf = cv2.imencode(".jpg", img)
    return img, base64.b64encode(buf).decode()

def get_ui_elements(max_depth=12, max_elements=400):
    elements = []
    try: root = auto.GetForegroundControl()
    except Exception: return elements

    def walk(ctrl, depth):
        if depth > max_depth or len(elements) >= max_elements: return
        try:
            r = ctrl.BoundingRectangle
            if r and r.width() > 0 and r.height() > 0:
                name = (ctrl.Name or "").strip()
                elements.append({"name": name, "type": ctrl.ControlTypeName,
                                 "left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom})
        except Exception: pass
        try:
            for child in ctrl.GetChildren(): walk(child, depth + 1)
        except Exception: pass

    walk(root, 0)
    return elements

def locate_target(step_data, ui_elements):
    target_name = (step_data.get("target_name") or "").lower().strip()
    if target_name and ui_elements:
        cands = [e for e in ui_elements if e["name"] and (target_name in e["name"].lower() or e["name"].lower() in target_name)]
        if cands:
            e = min(cands, key=lambda e: (e["right"]-e["left"]) * (e["bottom"]-e["top"]))
            return (e["left"], e["top"], e["right"], e["bottom"]), "Windows OS"

    bbox = step_data.get("target_bbox_2d")
    if isinstance(bbox, list) and len(bbox) == 4 and any(bbox):
        ymin, xmin, ymax, xmax = bbox
        # Qwen returns normalized 0-1000 
        if max(ymin, xmin, ymax, xmax) <= 1000 and max(SCREEN_W, SCREEN_H) > 1000:
            left,  right  = int(xmin / 1000 * SCREEN_W), int(xmax / 1000 * SCREEN_W)
            top,   bottom = int(ymin / 1000 * SCREEN_H), int(ymax / 1000 * SCREEN_H)
        else:
            left, top, right, bottom = int(xmin), int(ymin), int(xmax), int(ymax)
        # normalize order + clamp to screen
        left, right  = sorted((max(0, min(left,  SCREEN_W)), max(0, min(right,  SCREEN_W))))
        top,  bottom = sorted((max(0, min(top,   SCREEN_H)), max(0, min(bottom, SCREEN_H))))
        if right > left and bottom > top:
            return (left, top, right, bottom), "Qwen Vision Grounding"

    return None, "none"

# overlays
class AnimatedOverlay(QWidget):
    def __init__(self, box, label):
        super().__init__()
        self.box, self.label = box, label
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(0, 0, SCREEN_W, SCREEN_H)
        self.alpha, self.fade_dir = 255, -1
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate_pulse)
        self.timer.start(30) 

    def animate_pulse(self):
        self.alpha += self.fade_dir * 12
        if self.alpha <= 100: self.alpha, self.fade_dir = 100, 1
        elif self.alpha >= 255: self.alpha, self.fade_dir = 255, -1
        self.update()

    def paintEvent(self, event):
        if not self.box: return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing) 
        l, t, r, b = self.box
        p.setPen(QPen(QColor(255, 45, 85, self.alpha), 3, Qt.DashLine))
        p.drawRect(l, t, r - l, b - t)
        p.setPen(QPen(QColor(255, 45, 85, 255), 5, Qt.SolidLine))
        length = min(20, (r - l) // 3) 
        p.drawLine(l, t + length, l, t); p.drawLine(l, t, l + length, t)
        p.drawLine(r - length, t, r, t); p.drawLine(r, t, r, t + length)
        p.drawLine(l, b - length, l, b); p.drawLine(l, b, l + length, b)
        p.drawLine(r - length, b, r, b); p.drawLine(r, b, r, b - length)
        p.setFont(QFont("Segoe UI", 14, QFont.Bold))
        text_x, text_y = l, max(t - 10, 20)
        p.setPen(QColor(0, 0, 0, 200)); p.drawText(text_x + 2, text_y + 2, self.label)
        p.setPen(QColor(255, 255, 255)); p.drawText(text_x, text_y, self.label)

class ControlPanel(QWidget):
    def __init__(self, n, total, instruction):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet("""
            QWidget { background-color: #e0e0e0; color: #000; border: 2px solid #fff; border-bottom-color: #888; border-right-color: #888; font-family: 'Segoe UI'; }
            QLabel { border: none; font-size: 14px; }
            QPushButton { background-color: #e0e0e0; font-weight: bold; padding: 6px; border: 2px solid #fff; border-bottom-color: #888; border-right-color: #888; }
            QPushButton:pressed { border: 2px solid #888; border-bottom-color: #fff; border-right-color: #fff; }
        """)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Step {n}</b> (Execute action to advance)"))
        msg = QLabel(instruction); msg.setWordWrap(True)
        lay.addWidget(msg)
        
        row = QHBoxLayout()
        self.stop_btn = QPushButton("Stop Guide")
        self.force_btn = QPushButton("I'm Stuck (Replan)") 
        row.addWidget(self.stop_btn); row.addWidget(self.force_btn)
        lay.addLayout(row)
        self.resize(360, 130)
        self.move(SCREEN_W - 390, SCREEN_H - 180)

class SuccessPanel(QWidget):
    def __init__(self, message):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet("QWidget { background-color: #e0e0e0; border: 3px solid #000; } QLabel { color: #008000; font-size: 20px; font-weight: bold; padding: 20px; border: none; } QPushButton { font-weight: bold; padding: 8px 30px; }")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"SUCCESS\n\n{message}"), alignment=Qt.AlignCenter)
        self.ok_btn = QPushButton("OK")
        lay.addWidget(self.ok_btn, alignment=Qt.AlignCenter)
        self.resize(450, 180)
        self.move((SCREEN_W - 450) // 2, (SCREEN_H - 180) // 2)

def show_success_screen(message):
    loop = QEventLoop()
    panel = SuccessPanel(message)
    panel.ok_btn.clicked.connect(loop.quit)
    panel.show(); loop.exec_(); panel.close(); app.processEvents()

#auto observation here
def show_step_and_wait(box, instruction, n, total, initial_img, initial_ui_hash):
    res = {"action": "timeout"}
    loop = QEventLoop()
    overlay, panel = AnimatedOverlay(box, f"{n}. {instruction}"), ControlPanel(n, total, instruction)
    
    def finish(act): 
        res["action"] = act
        loop.quit()
        
    panel.stop_btn.clicked.connect(lambda: finish("stop"))
    panel.force_btn.clicked.connect(lambda: finish("fail"))

    #listening the hardware changes
    user_acted = [False]

    def on_click(x, y, button, pressed):
        if not pressed: # mouse release trig
            user_acted[0] = True

    def on_key_release(key):
        #enter key trig
        if key in [keyboard.Key.enter, keyboard.Key.tab]:
            user_acted[0] = True

    mouse_listener = mouse.Listener(on_click=on_click)
    key_listener = keyboard.Listener(on_release=on_key_release)
    mouse_listener.start()
    key_listener.start()

# visual resoning
    def check_for_changes():
        # ingnoring cursors
        if not user_acted[0]:
            return

        print("\n[preemptive action] Click/Drag detected. Verifying UI state")
        # removing overlay before mss
       
        overlay.hide(); panel.hide(); app.processEvents()
        time.sleep(0.6)

        new_img, _ = capture_screen()

        # check both from the preemptive ingestion of VUI and the VLM gen UI for the delta change
        els = get_ui_elements()
        new_hash = hashlib.md5(ui_signature(els).encode('utf-8')).hexdigest()

        if new_hash != initial_ui_hash:
            print("[VERIFIED]: UI structure changed. Advancing...")
            finish("auto_advanced")
            return

        # visual pix diff calc
        diff = cv2.absdiff(initial_img, new_img)
        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
        changed_pixels = cv2.countNonZero(thresh)

        if (changed_pixels / TOTAL_PIXELS) > 0.005:  # 0.5% screen change threshold
            print(f"[VERIFIED]: Pixel shift detected ({changed_pixels}px). Advancing...")
            finish("auto_advanced")
            return

        print("[FALSE ALARM]: Click did not change the software. Waiting for next action.")
        user_acted[0] = False       #
        overlay.show(); panel.show()  # restore UI and keep waiting

    observer_timer = QTimer()
    observer_timer.timeout.connect(check_for_changes)


    observer_timer.start(250)
    
    overlay.show(); panel.show(); loop.exec_()
    
    # Cleaning
    observer_timer.stop(); overlay.timer.stop() 
    mouse_listener.stop(); key_listener.stop()
    overlay.close(); panel.close(); app.processEvents()
    return res["action"]


    ###########main loop
def guide_session(query, focus_delay=3):
    print(f"\n>> Target set: '{query}'")
    time.sleep(focus_delay)

    step_counter = 1
    last_action_failed = False

    previous_b64 = None
    last_instruction_executed = "None (This is the starting state. The goal cannot be complete yet.)"
    while step_counter <= MAX_STEPS:
        print(f"\n--- STEP {step_counter} ---")
        current_img, b64 = capture_screen()
        current_els = get_ui_elements()

        ui_hint = ui_signature(current_els)
        current_ui_hash = hashlib.md5(ui_hint.encode('utf-8')).hexdigest()

        try:
            # senging previous frame alongside the current frame consecutivcely to server
            res = requests.post(SERVER_URL, json={
                "question": query,
                "screen_b64": b64,
                "previous_b64": previous_b64,
                "ui_hint": ui_hint,
                "last_action_failed": last_action_failed,
                "last_action": last_instruction_executed
            }, timeout=REQUEST_TIMEOUT)
            if res.status_code != 200:
                print(f"\n[SERVER ERROR {res.status_code}]: {res.text}")
                return
            plan = res.json()
        except requests.exceptions.Timeout:
            print(f"Server timed out after {REQUEST_TIMEOUT}s \tStopping.")
            return
        except Exception as e:
            print(f"Error connecting to server: {e}")
            return

        if plan.get("status") == "COMPLETE":
            success_msg = plan.get("instruction", "query performed")
            print(f"\n  REASINING  start: {plan.get('thought')}")
            print(f" stat: {success_msg}")
            show_success_screen(success_msg)
            break
            
        target_name = plan.get("target_name", "")
        box, src = locate_target(plan, current_els)
        instruction = plan.get("instruction", "")
        print(f"Action: {instruction} (target='{target_name}', via {src})")
        
        action = show_step_and_wait(box, instruction, step_counter, "?", current_img, current_ui_hash)
        
        if action == "stop": 
            break
        elif action == "fail":
            print("\n🚨 User forced a replan. Invalidating memory...")
            last_action_failed = True
        elif action == "auto_advanced":
            last_action_failed = False
            time.sleep(1) # Buffer time for UI animations to finish rendering
            
        # The current frame becomes the "previous" frame for the next step's contrast
        last_instruction_executed = instruction
        previous_b64 = b64
        step_counter += 1
    else:
        # while-loop finished without break -> hit the step cap
        print(f"\nReached the {MAX_STEPS}-step limit; stopping to avoid an endless loop.")

def run():
    print("="*50)
    print("ONGC AUTONOMOUS DESKTOP GUIDE")
    print("="*50)
    while True:
        q = input("\nWhat is your query? [exit to exit]: ").strip()
        if q.lower() in ['exit', 'quit']: break
        if q: guide_session(q)

if __name__ == "__main__":
    run()