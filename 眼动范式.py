# -*- coding: utf-8 -*-
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
GRID = 5                  # 支持 3 / 5 等奇数网格，中心格自动跳过采集
TARGET_SHOW = 1.0         # 目标点显示时长（秒） - 可根据志愿者反应速度调大
REST_TIME = 2.0           # 每次试次间休息时长（秒） - 可根据志愿者反应速度调大
REPEAT_PER_CELL = 2       # 普通点位采集次数
DIRECTION_REST = 3.0      # 自动切换方向休息时长(秒)
MANUAL_CONFIRM_DIRECTION = False  # 切换新方向是否需要医生按回车手动确认（适于反应迟钝/老年患者）

# 计算所有目标点的中心坐标
cell_w = screen_width // GRID    # 单个格子宽度
cell_h = screen_height // GRID   # 单个格子高度
positions = []
pos_index_map = []  # 记录每个点位对应的行列号
for row in range(GRID):
    for col in range(GRID):
        cx = col * cell_w + cell_w // 2
        cy = row * cell_h + cell_h // 2
        positions.append((cx, cy))
        pos_index_map.append((row, col))

total_pos = len(positions)  # 总点位数量

# 计算【网格中心行列】，奇数网格专用（3/5/7...）
center_row = GRID // 2
center_col = GRID // 2

# 全局控制变量
paused = False  # 暂停标志
running = True  # 运行标志

# ========================== 3. 画布（全屏绘制） ==========================
# 创建一块全屏画布，所有显示内容都画在这上面
canvas = tk.Canvas(root, bg="black", highlightthickness=0)
canvas.place(x=0, y=0, width=screen_width, height=screen_height)


def check_window_exists():
    """安全地检测 Tkinter 主窗口和画布是否存在，防止调用 winfo_exists 抛出 TclError"""
    if not running:
        return False
    try:
        return root.winfo_exists() and canvas.winfo_exists()
    except tk.TclError:
        return False


# ========================== 4. 绘制淡灰色网格 ==========================
def draw_grid():
    """绘制淡灰色网格线框，不刺眼、不影响眼动"""
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        grid_color = "#333333"  # 深灰色，柔和不刺眼
        for i in range(1, GRID):
            # 竖线
            x = i * cell_w
            canvas.create_line(x, 0, x, screen_height, fill=grid_color, width=2)
            # 横线
            y = i * cell_h
            canvas.create_line(0, y, screen_width, y, fill=grid_color, width=2)
    except tk.TclError:
        pass


# ========================== 5. 显示功能函数 ==========================
def set_text(txt, font=FONT_LARGE):
    """显示文字提示（自动清空屏幕+显示网格+显示文字）"""
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
    """显示屏幕中心白色十字（注视点）"""
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        draw_grid()
        cx, cy = screen_width // 2, screen_height // 2
        canvas.create_line(cx - 30, cy, cx + 30, cy, fill="white", width=4)
        canvas.create_line(cx, cy - 30, cx, cy + 30, fill="white", width=4)
        root.update()
    except tk.TclError:
        pass


def show_target(x, y):
    """显示红色目标圆点（眼动目标）"""
    if not running:
        return
    try:
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        draw_grid()
        r = 40  # 圆点半径
        canvas.create_oval(x - r, y - r, x + r, y + r, fill="red", outline="red")
        root.update()
    except tk.TclError:
        pass


def speak(text):
    """语音提示函数"""
    engine = pyttsx3.init()
    engine.setProperty('rate', 180)  # 语速
    print(f"语音：{text}")
    engine.say(text)
    engine.runAndWait()
    engine.stop()
    del engine
    time.sleep(0.1)


# ========================== 6. 键盘控制函数 ==========================
def key_control(e):
    """全局快捷键：ESC退出 / 空格暂停 / 回车继续"""
    global paused, running
    if e.keysym == "Escape":
        running = False
        try:
            common.stop_daq()
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


# 绑定键盘事件
root.bind("<Key>", key_control)


# ========================== 7. 等待开始（按回车启动） ==========================
def wait_start():
    """程序启动后，等待按回车键才开始"""
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

# 提权进程，初始化网络与本地日志
common.elevate_process_priority()
common.init_udp()
common.init_log(patient_name, "眼动网格")
common.start_daq("眼动网格")

# ========================== 9. 主实验循环 ==========================
# 遍历所有点位
for idx, (tx, ty) in enumerate(positions):
    if not check_window_exists():
        break

    # 获取当前点位的行列
    curr_row, curr_col = pos_index_map[idx]

    # 切换新点位：清空画面并绘制网格
    canvas.delete("all")
    draw_grid()
    root.update()
    common.precise_wait(0.3, root, lambda: paused, lambda: running)

    # 判断：是否为【中心格】，中心格直接跳过采集
    if curr_row == center_row and curr_col == center_col:
        print(f"\n===== 第 {idx + 1}/{total_pos} 个位置 → 中心格，跳过采集 =====")
        speak("当前为中心位置，跳过采集")
        set_text("中心位置，跳过采集")
        common.precise_wait(1.0, root, lambda: paused, lambda: running)

        # 中心格切换逻辑
        if idx != 0:
            if MANUAL_CONFIRM_DIRECTION:
                set_text(f"准备第 {idx + 1} 个方向\n按回车键开始当前方向采集")
                wait_start()
            else:
                set_text(f"切换方向，休息 {int(DIRECTION_REST)} 秒")
                common.precise_wait(DIRECTION_REST, root, lambda: paused, lambda: running)

        show_center_cross()
        common.precise_wait(0.5, root, lambda: paused, lambda: running)
        continue  # 跳过当前点位所有采集逻辑

    # -------- 非中心格：正常采集流程 --------
    # 语音+文字提示当前点位
    print(f"\n===== 第 {idx + 1}/{total_pos} 个位置 =====")
    speak(f"准备采集第{idx + 1}个方向")
    set_text(f"准备第{idx + 1}个方向")
    common.precise_wait(1.0, root, lambda: paused, lambda: running)

    # 第一个方向不执行切换休息，其余方向休息
    if idx != 0:
        if MANUAL_CONFIRM_DIRECTION:
            set_text(f"准备第 {idx + 1} 个方向\n按回车键开始当前方向采集")
            wait_start()
        else:
            set_text(f"切换方向，休息 {int(DIRECTION_REST)} 秒")
            common.precise_wait(DIRECTION_REST, root, lambda: paused, lambda: running)
    else:
        show_center_cross()

    # 当前点位 循环采集指定次数
    for i in range(REPEAT_PER_CELL):
        if not check_window_exists():
            break

        # 1. 休息时长：显示中心十字，触发事件标记
        show_center_cross()
        common.log_event(trial_idx=i+1, grid_row=curr_row, grid_col=curr_col, px=screen_width//2, py=screen_height//2, event_type="REST_START", desc=f"方向{idx+1}_试次{i+1}_休息开始")
        common.precise_wait(REST_TIME, root, lambda: paused, lambda: running)
        common.log_event(trial_idx=i+1, grid_row=curr_row, grid_col=curr_col, px=screen_width//2, py=screen_height//2, event_type="REST_END", desc=f"方向{idx+1}_试次{i+1}_休息结束")

        # 2. 显示目标红点，触发事件标记
        show_target(tx, ty)
        common.log_event(trial_idx=i+1, grid_row=curr_row, grid_col=curr_col, px=tx, py=ty, event_type="TARGET_START", desc=f"方向{idx+1}_试次{i+1}_红点显示")
        common.precise_wait(TARGET_SHOW, root, lambda: paused, lambda: running)
        common.log_event(trial_idx=i+1, grid_row=curr_row, grid_col=curr_col, px=tx, py=ty, event_type="TARGET_END", desc=f"方向{idx+1}_试次{i+1}_红点消失")

    # 当前点位所有试次跑完，立刻切回中心十字
    if check_window_exists():
        show_center_cross()
        common.precise_wait(0.5, root, lambda: paused, lambda: running)

# 全部点位结束后，再次确认显示中心十字
if check_window_exists():
    show_center_cross()
    common.precise_wait(1.0, root, lambda: paused, lambda: running)

# ========================== 10. 实验结束 ==========================
# 停止远端 cDAQ 采集
common.stop_daq()

if check_window_exists():
    set_text("✅ 实验全部完成！", font=FONT_LARGE)
    speak("实验完成")
    common.precise_wait(3.0, root, lambda: paused, lambda: running)

# 正常退出
if check_window_exists():
    try:
        root.destroy()
    except:
        pass
sys.exit(0)