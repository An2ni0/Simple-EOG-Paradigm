# -*- coding: utf-8 -*-
"""
眼电EOG采集范式 - 十字方向眼动采集 (基于 Neuracle TriggerBox 硬件打标控制)
"""
import time
import pyttsx3
import winsound
import tkinter as tk
from tkinter import simpledialog
import sys
import os

# 导入公共辅助库
import common

# ========================== 1. 初始化全屏窗口 ==========================
# 创建TK主窗口
root = tk.Tk()
root.title("眼电EOG采集范式 - 网格范式")

# 弹窗输入患者信息（在进入全屏前执行，避免全屏下弹窗焦点丢失）
root.withdraw()  # 暂时隐藏主窗口
patient_name = simpledialog.askstring("录入信息", "请输入患者姓名/编号:", parent=root)
if not patient_name:
    patient_name = "subject"
root.deiconify()  # 恢复主窗口

root.attributes("-fullscreen", True)  # 设置全屏
root.configure(bg="black")  # 背景黑色
root.overrideredirect(True)  # 去掉窗口边框（纯全屏）

# ========================== 2. 屏幕尺寸与基础参数 ==========================
# 获取屏幕真实宽高
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()

# 字体设置（大号提示语 / 小号提示语）
FONT_LARGE = ("SimHei", 60, "bold")
FONT_SMALL = ("SimHei", 32, "bold")

# ========================== 实验核心参数 ==========================
GRID = common.config["paradigm"]["grid_size"]
TARGET_SHOW = common.config["paradigm"]["target_show_sec"]
REST_TIME = common.config["paradigm"]["rest_time_sec"]
REPEAT_PER_CELL = common.config["paradigm"]["repeat_per_cell"]
DIRECTION_REST = common.config["paradigm"]["direction_rest_sec"]
MANUAL_CONFIRM_DIRECTION = common.config["paradigm"]["manual_confirm_direction"]

cell_w = screen_width // GRID
cell_h = screen_height // GRID
positions = []
pos_index_map = []

# ========== 按指定顺序生成点位：X负 → X正 → Y正 → Y负 → 原点(眨眼) ==========
center_r = GRID // 2
center_c = GRID // 2

# 1. X轴 负半轴 (行=中心行，列 < 中心列)
x_neg_start = len(positions)
for col in range(center_c - 1, -1, -1):
    cx = col * cell_w + cell_w // 2
    cy = center_r * cell_h + cell_h // 2
    positions.append((cx, cy))
    pos_index_map.append((center_r, col))
x_neg_end = len(positions) - 1

# 2. X轴 正半轴 (行=中心行，列 > 中心列)
x_pos_start = len(positions)
for col in range(center_c + 1, GRID):
    cx = col * cell_w + cell_w // 2
    cy = center_r * cell_h + cell_h // 2
    positions.append((cx, cy))
    pos_index_map.append((center_r, col))
x_pos_end = len(positions) - 1

# 3. Y轴 正半轴 (列=中心列，行 < 中心行)
y_pos_start = len(positions)
for row in range(center_r - 1, -1, -1):
    cx = center_c * cell_w + cell_w // 2
    cy = row * cell_h + cell_h // 2
    positions.append((cx, cy))
    pos_index_map.append((row, center_c))
y_pos_end = len(positions) - 1

# 4. Y轴 负半轴 (列=中心列，行 > 中心行)
y_neg_start = len(positions)
for row in range(center_r + 1, GRID):
    cx = center_c * cell_w + cell_w // 2
    cy = row * cell_h + cell_h // 2
    positions.append((cx, cy))
    pos_index_map.append((row, center_c))
y_neg_end = len(positions) - 1

# 5. 最后添加 原点(中心格)：用于眨眼采集，不再跳过
origin_start = len(positions)
cx = center_c * cell_w + cell_w // 2
cy = center_r * cell_h + cell_h // 2
positions.append((cx, cy))
pos_index_map.append((center_r, center_c))
origin_end = len(positions) - 1

total_pos = len(positions)
center_row = GRID // 2
center_col = GRID // 2

# ========== 新增：计算X负半轴总试次，作为原点眨眼总次数 ==========
x_neg_point_count = x_neg_end - x_neg_start + 1
BLINK_TOTAL_COUNT = x_neg_point_count * REPEAT_PER_CELL
# 眨眼闪烁单周期时长(亮+灭)，可微调：0.3s 接近自然眨眼频率
BLINK_CYCLE = 0.3

# 全局控制变量
paused = False
running = True

# ========================== 3. 画布（全屏绘制） ==========================
canvas = tk.Canvas(root, bg="black", highlightthickness=0)
canvas.place(x=0, y=0, width=screen_width, height=screen_height)


def check_window_exists():
    if not running:
        return False
    try:
        return root.winfo_exists() and canvas.winfo_exists()
    except tk.TclError:
        return False

# ========================== 4. 绘制淡灰色网格 ==========================
def draw_grid():
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        grid_color = "#333333"
        for i in range(1, GRID):
            x = i * cell_w
            y = i * cell_h
            canvas.create_line(x, 0, x, screen_height, fill=grid_color, width=2)
            canvas.create_line(0, y, screen_width, y, fill=grid_color, width=2)
    except tk.TclError:
        pass

# ========================== 5. 显示功能函数 ==========================
def set_text(txt, font=FONT_LARGE):
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        draw_grid()
        canvas.create_text(screen_width // 2, screen_height // 2,
                           text=txt, font=font, fill="white")
        root.update()
    except tk.TclError:
        pass


def show_center_cross():
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        draw_grid()
        cx = screen_width // 2
        cy = screen_height // 2
        canvas.create_line(cx - 30, cy, cx + 30, cy, fill="white", width=4)
        canvas.create_line(cx, cy - 30, cx, cy + 30, fill="white", width=4)
        root.update()
    except tk.TclError:
        pass


def show_target(x, y):
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        draw_grid()
        r = 40
        canvas.create_oval(x - r, y - r, x + r, y + r, fill="red", outline="red")
        root.update()
    except tk.TclError:
        pass

# 新增：原点高频闪烁（模拟连续眨眼）
def blink_flash(target_x, target_y, cycle_time, root_win, pause_func, run_func):
    half = cycle_time / 2
    r = 40
    for _ in range(2):
        if not run_func() or pause_func():
            break
        # 红点亮起
        canvas.delete("all")
        draw_grid()
        canvas.create_oval(target_x - r, target_y - r, target_x + r, target_y + r, fill="red", outline="red")
        root_win.update()
        common.precise_wait(half, root_win, pause_func, run_func)

        if not run_func() or pause_func():
            break
        # 红点熄灭（只保留网格）
        canvas.delete("all")
        draw_grid()
        root_win.update()
        common.precise_wait(half, root_win, pause_func, run_func)


def speak(text):
    engine = pyttsx3.init()
    engine.setProperty('rate', 180)
    print(f"语音：{text}")
    engine.say(text)
    engine.runAndWait()
    engine.stop()
    del engine
    time.sleep(0.1)

# ========================== 6. 键盘控制函数 ==========================
def key_control(e):
    global paused, running
    if e.keysym == "Escape":
        running = False
        try:
            common.stop_daq()
        except:
            pass
        try:
            common.disconnect_triggerbox()
        except:
            pass
        try:
            root.destroy()
        except:
            pass
        sys.exit(0)
    if e.keysym == "space":
        paused = True
        set_text("⏸ 已暂停\n按回车键继续")
    if e.keysym == "Return":
        paused = False

root.bind("<Key>", key_control)

# ========================== 7. 等待开始 ==========================
def wait_start():
    global paused
    paused = True
    while paused and check_window_exists():
        try:
            root.update()
        except tk.TclError:
            break
        time.sleep(0.02)

# ========================== 8. 实验启动流程 ==========================
draw_grid()
set_text("眼电采集准备开始")
speak("眼电采集程序准备开始，请眼睛跟随红点看，休息时看中心十字")
time.sleep(1)

set_text("按回车键开始实验\nESC退出  空格暂停")
wait_start()

common.elevate_process_priority()
common.init_udp()
common.init_log(patient_name, "眼动网格")
common.connect_triggerbox()

# ========================== 9. 主实验循环 ==========================
for idx, (tx, ty) in enumerate(positions):
    if not check_window_exists():
        break

    curr_row, curr_col = pos_index_map[idx]

    # ========== 分段判断：进入当前分段第一个点位 → 启动DAQ ==========
    if idx == x_neg_start:
        print("===== X负半轴采集开始，启动DAQ =====")
        common.start_daq("眼动网格_X负半轴")
    elif idx == x_pos_start:
        print("===== X正半轴采集开始，启动DAQ =====")
        common.start_daq("眼动网格_X正半轴")
    elif idx == y_pos_start:
        print("===== Y正半轴采集开始，启动DAQ =====")
        common.start_daq("眼动网格_Y正半轴")
    elif idx == y_neg_start:
        print("===== Y负半轴采集开始，启动DAQ =====")
        common.start_daq("眼动网格_Y负半轴")
    elif idx == origin_start:
        print("===== 原点眨眼采集开始，启动DAQ =====")
        common.start_daq("眼动网格_眨眼")

    canvas.delete("all")
    draw_grid()
    root.update()
    common.precise_wait(0.3, root, lambda: paused, lambda: running)

    # 判断是否为原点(中心格)：提示眨眼，不再跳过
    if curr_row == center_row and curr_col == center_col:
        print(f"\n===== 第 {idx + 1}/{total_pos} 个位置 → 原点（眨眼采集） =====")
        speak("现在请连续眨眼")
        set_text("请连续眨眼")
        common.precise_wait(1.0, root, lambda: paused, lambda: running)

        if idx != 0:
            if MANUAL_CONFIRM_DIRECTION:
                set_text(f"准备第 {idx + 1} 个方向\n按回车键开始")
                wait_start()
            else:
                set_text(f"准备眨眼，休息 {int(DIRECTION_REST)} 秒")
                common.precise_wait(DIRECTION_REST, root, lambda: paused, lambda: running)

        # 眨眼试次循环
        total_blink = BLINK_TOTAL_COUNT
        for blink_idx in range(total_blink):
            if not check_window_exists():
                break

            # 休息阶段：不打任何标签
            show_center_cross()
            common.precise_wait(REST_TIME, root, lambda: paused, lambda: running)

            # 眨眼开始 + 前置标签
            common.log_event(
                trial_idx=blink_idx+1,
                grid_row=curr_row,
                grid_col=curr_col,
                px=tx, py=ty,
                event_type="BLINK_BEFORE",
                desc=f"眨眼动作开始"
            )
            blink_flash(tx, ty, BLINK_CYCLE, root, lambda: paused, lambda: running)
            # 眨眼结束 + 后置标签
            common.log_event(
                trial_idx=blink_idx+1,
                grid_row=curr_row,
                grid_col=curr_col,
                px=tx, py=ty,
                event_type="BLINK_AFTER",
                desc=f"眨眼动作结束"
            )

    else:
        # 普通红点点位
        print(f"\n===== 第 {idx + 1}/{total_pos} 个位置 =====")
        speak(f"准备采集第{idx + 1}个方向")
        set_text(f"准备第{idx + 1}个方向")
        common.precise_wait(1.0, root, lambda: paused, lambda: running)

        if idx != 0:
            if MANUAL_CONFIRM_DIRECTION:
                set_text(f"准备第 {idx + 1} 个方向\n按回车键开始")
                wait_start()
            else:
                set_text(f"切换方向，休息 {int(DIRECTION_REST)} 秒")
                common.precise_wait(DIRECTION_REST, root, lambda: paused, lambda: running)
        else:
            show_center_cross()

        # 普通试次循环
        for i in range(REPEAT_PER_CELL):
            if not check_window_exists():
                break

            # 休息阶段：不打任何标签
            show_center_cross()
            common.precise_wait(REST_TIME, root, lambda: paused, lambda: running)

            # 红点亮起 + 前置标签
            show_target(tx, ty)
            common.log_event(
                trial_idx=i+1,
                grid_row=curr_row,
                grid_col=curr_col,
                px=tx, py=ty,
                event_type="TARGET_BEFORE",
                desc=f"红点动作开始"
            )
            # 红点纯停留 1 秒
            common.precise_wait(TARGET_SHOW, root, lambda: paused, lambda: running)
            # 红点结束标签
            common.log_event(
                trial_idx=i+1,
                grid_row=curr_row,
                grid_col=curr_col,
                px=tx, py=ty,
                event_type="TARGET_AFTER",
                desc=f"红点动作结束"
            )

    # 单个点位结束切回中心十字
    if check_window_exists():
        show_center_cross()
        common.precise_wait(0.5, root, lambda: paused, lambda: running)

    # 分段停止DAQ
    if idx == x_neg_end:
        print("===== X负半轴采集结束，停止DAQ =====")
        common.stop_daq()
    elif idx == x_pos_end:
        print("===== X正半轴采集结束，停止DAQ =====")
        common.stop_daq()
    elif idx == y_pos_end:
        print("===== Y正半轴采集结束，停止DAQ =====")
        common.stop_daq()
    elif idx == y_neg_end:
        print("===== Y负半轴采集结束，停止DAQ =====")
        common.stop_daq()
    elif idx == origin_end:
        print("===== 原点眨眼采集结束，停止DAQ =====")
        common.stop_daq()

# 全部点位结束
if check_window_exists():
    show_center_cross()
    # 修复：补齐全部4个参数
    common.precise_wait(1.0, root, lambda: paused, lambda: running)

if check_window_exists():
    set_text("✅ 实验全部完成！", font=FONT_LARGE)
    # 修复：补齐全部4个参数
    common.precise_wait(3.0, root, lambda: paused, lambda: running)

if check_window_exists():
    try:
        common.disconnect_triggerbox()
    except:
        pass
    try:
        root.destroy()
    except:
        pass
sys.exit(0)