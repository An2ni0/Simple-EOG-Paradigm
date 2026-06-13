# EOG Grid Acquisition System User Manual & Doctor Operation SOP (JSON Config Version)

[English Version](#english-version) | [中文版](#chinese-version)

---

## <a id="english-version"></a> English Version

This system is a simplified dual-machine system designed specifically for the **synchronous acquisition of Electrooculography (EOG) and eye gaze fixation points**. This version implements **complete decoupling of hardware/software parameters and code**. All adjustable options, such as network IPs, ports, hardware card numbers, channel assignments, and experiment timing, are uniformly stored in the `config.json` configuration file, making it convenient for collaborators to quickly debug and deploy in different laboratory environments.

---

## I. Hardware & Software Decoupling and Unified Configuration (config.json)

The [config.json](file:///%5BIP_ADDRESS%5D/config.json) in the project root directory is the only configuration file for the system. Open it to adjust all the following parameters:

```json
{
    "network": {
        "daq_pc_ip": "[IP_ADDRESS]",          // Static LAN IP of the receiver (cDAQ PC)
        "udp_port": 55555                     // UDP network communication port
    },
    "daq_hardware": {
        "eog_emg_dev": "cDAQ1Mod8",           // Slot name where the analog EOG/EMG card is located
        "eog_emg_chans": ["ai0", "ai2", "ai6"], // Enabled analog input channels
        "__comment_channel_mappings__": "Horizontal electrode default ai0(hEOG), Right eye vertical electrode default ai2(vEOG_right), Left eye vertical electrode default ai6(vEOG_left)",
        "channel_mappings": {                 // Mapping between physiological signals and DAQ channels
            "hEOG": "ai0",                    // Horizontal EOG signal
            "vEOG_right": "ai2",              // Right eye vertical EOG signal
            "vEOG_left": "ai6"                // Left eye vertical EOG signal
        },
        "sample_rate": 10000,                 // NI card hardware sampling rate (Default 10kHz)
        "display_seconds": 5                  // Time span of waveforms displayed on the cDAQ monitor GUI (seconds)
    },
    "paradigm": {
        "grid_size": 5,                       // Experimental stimulus grid size (5x5 odd grid)
        "target_show_sec": 1.0,               // Duration the red target dot is lit (seconds)
        "rest_time_sec": 2.0,                 // Duration the center cross (rest) is displayed between trial switches (seconds)
        "repeat_per_cell": 2,                 // Number of repeated acquisitions per grid point
        "direction_rest_sec": 3.0,            // Auto-rest time when switching to the next point direction (seconds)
        "manual_confirm_direction": false     // Whether to enable manual confirmation mode for direction switching (manual progress control)
    }
}
```

> **💡 Note**: If `config.json` is not detected upon system startup, it will automatically create a file with the default parameters above in the root directory. After modifying this file, no recompilation is needed; just restart the program for the changes to take effect.

---

## II. Hardware DIFF Wiring and COM Reference Pin Description

To ensure high-precision bioelectric signal acquisition and eliminate common-mode noise, the system adopts **DIFF (Differential Input)** mode:
* **Channel Differential Wiring Pairing**:
  * Horizontal EOG channel mapped to `ai0`: Physical positive connected to `ai0`, physical negative connected to `ai8`.
  * Right eye vertical electrode channel mapped to `ai2`: Physical positive connected to `ai2`, physical negative connected to `ai10`.
  * Left eye vertical electrode channel mapped to `ai6`: Physical positive connected to `ai6`, physical negative connected to `ai14`.
* **Reference Electrode (REF)**: The reference electrode (such as the mastoid reference point behind the ear) should be physically connected to the **`COM`** ground input port of the NI-9205 card.

---

## III. Doctor and Student Experiment Operation SOP (Standard Operating Procedure)

Please strictly follow the steps below to conduct the experiment. Do not reverse the order:

### Step 1: Start cDAQ Acquisition Software (Operate on cDAQ PC)
1. Ensure the NI acquisition box is powered on and the USB is connected.
2. Double-click to run or run in the command line:
    ```bash
    python DAQ_GUI_server.py
    ```
3. **Check Preview**: Confirm the window is open, the status bar shows **"🟢 Preview Mode (Waiting for CMD_START Command)"**, and the borders are green. The real-time waveform refresh indicates normal operation.

### Step 2: Start Paradigm Main Program (Operate on Paradigm Control PC)
1. **Modify Configuration (If network or time changes)**: Open `config.json` in the root directory and ensure `daq_pc_ip` is filled with the actual wired network card IP address of the cDAQ PC just checked.
2. Run on the paradigm PC:
    ```bash
    python 眼动范式.py
    ```
3. **Information Entry**: An input box will pop up in the center of the screen, prompting **"Please enter patient name/ID"**.
4. Enter the patient identifier (e.g., `002_JohnDoe`) and click "OK" to confirm.

### Step 3: Device Linkage Verification
1. After input confirmation, the paradigm PC enters a full-screen black-background grid interface and displays **"Press ENTER to start the experiment"**.
2. **Check Linkage Status**: At this point, the paradigm computer automatically sends a start signal. **Please turn your head to check the cDAQ PC screen**:
    * The cDAQ GUI status bar MUST change to **"🔴 RECORDING"**, the window borders turn **red**, and the timer starts running.
    * *Troubleshooting*: If it does not turn red, it means the network is disconnected, the IP is misconfigured, or the firewall is blocking it. Please press `ESC` to exit, troubleshoot the configuration, and then restart!

### Step 4: Execute Experiment
1. Instruct the volunteer to get ready and stare at the screen.
2. Press the **"ENTER"** key on the paradigm PC.
3. **Volunteer Task**:
    * When a **red dot** appears on the screen, please stare closely at the red dot.
    * When the red dot disappears and a **white cross** appears in the center of the screen, quickly move your eyes back to stare at the center cross.
4. The experiment will automatically traverse the 25 positions of the grid in sequence (automatically skipping the very center grid point).

### Step 5: Experiment End and Safe Storage
1. After the point traversal is complete, the paradigm PC will play the voice "Experiment Completed" and safely exit full screen.
2. The cDAQ PC receives the automatically sent stop command, the interface reverts to **"🟢 Green Preview State"**, and raw binary data files `.bin` and session description files `_meta.json` are generated in the `EOG/` directory.
3. The paradigm PC will generate a local behavioral CSV log in the `logs/` directory.

---

## IV. Keyboard Shortcut Controls During the Experiment

On the paradigm PC, operators can control the experiment progress at any time via the keyboard:
* `SPACE bar`: **Pause** the experiment (the screen prompts "Paused"). Press the `ENTER` key again to resume the experiment at the current position.
* `ESC key`: **Force abort** the experiment. The program will immediately send a stop signal to cDAQ and exit safely. **The acquired data segments will still be safely saved to disk**.
* `ENTER key`: Used to **resume the experiment** in the paused state, and to switch directions in the manual confirmation mode.

---

## V. Parameter Adjustment Guide for Slow-Reacting/Elderly Volunteers

If you encounter patients with slow reactions, difficult eye movements, or who get tired easily, please directly modify the `paradigm` configuration in `config.json`:

1. **Insufficient Red Dot Fixation Time**:
    Extend `"target_show_sec"` to `1.5` or `2.0` (seconds) to give patients enough time (> 1 second) to maintain stable fixation after moving their gaze.
2. **Fatigue When Switching Points / Unstable Baseline**:
    Extend `"rest_time_sec"` to `2.5` or `3.0` (seconds) to give patients ample time to move their eyes back to the center and stabilize, ensuring a thoroughly stable baseline.
3. **Patient is Extremely Prone to Fatigue and Cannot Cooperate with Automatic Continuous Switching**:
    Change `"manual_confirm_direction"` to `true`.
    * **Effect**: Before the system prepares to switch to each new point direction on the grid, the screen will display "Prepare for the next direction" and play a voice prompt. **At this point, the experiment hangs indefinitely waiting**.
    * The doctor can verbally instruct the patient to close their eyes and rest. After the patient is ready, **the doctor presses the ENTER key**, and the paradigm will begin presenting the red dots for that direction. This greatly provides breathing room and adjustment time.

---

## VI. Independent Dual-Machine Synchronization Latency Analysis Tool (analyze_sync_latency.py)

To conveniently evaluate the dual-machine UDP transmission latency and jitter under different network speeds and computer states, the system provides an independent analysis script [analyze_sync_latency.py](file:///c:/Users/simia/OneDrive/Data/DoCs/Tools/Simple_EOG_Paradigm/analyze_sync_latency.py):

### 1. Real-Time Network Latency and Clock Offset Measurement (Ping-Pong)
Uses the NTP algorithm to detect real-time one-way network latency, RTT jitter, and system clock offset between the two computers.
* **Server (Execute on DAQ PC)**:
  ```bash
  python analyze_sync_latency.py server
  ```
* **Client (Execute on Paradigm PC)**:
  ```bash
  python analyze_sync_latency.py client --ip 10.10.10.100
  ```
  *(Note: If the ip parameter is not specified, the program reads daq_pc_ip from config.json by default)*
* **Output Metrics**:
  * RTT Jitter (StdDev): If < 5ms, the LAN connection is very stable.
  * System Clock Offset: Indicates the millisecond-level time difference between the DAQ PC and Paradigm PC system clocks.

### 2. Offline Log Timestamp Comparison (Analyze)
Matches the behavioral CSV log generated locally after the experiment ends with the event marker sequence in the `_meta.json` saved by DAQ.
* **Run Analysis**:
  ```bash
  python analyze_sync_latency.py analyze logs/subject_眼动_20260611_120000.csv EOG/DAQ_Data_20260611_120000_meta.json -v
  ```
* **Output Metrics**:
  * **Mean Offset**: The average alignment time difference caused by the inconsistent clocks of the two PCs during this experiment. In subsequent analysis, subtracting this Offset from the EOG signal timestamp or event marker can perfectly align them.
  * **Jitter (StdDev)**: Real-time jitter of network transmission and thread scheduling. If Jitter < 10ms, it means no hardware alignment is needed; pure UDP linkage is sufficient.
  * **Clock Drift**: Evaluates the clock stretching/shrinking caused by slight deviations in the physical crystal oscillator frequencies of the two computers during the experiment.

---

## VII. Subsequent Modeling and Data Analysis Alignment Guide (For Analysts)

This system uses a minimalist UDP alignment scheme without needing to parse voltage levels:

1. **Read Metadata**:
    Open the `_meta.json` in the `EOG/` folder to read the marker points broadcasted by the paradigm PC in the `events` list. For example:
    ```json
    {
        "event": "T_1_R0C1_TARGET_START",
        "system_time": 17839304.51,
        "daq_sample_index": 25000
    }
    ```
    This indicates that when the cDAQ acquired the `25000`th data point, the red dot at the grid `(Row 0, Col 1)` position lit up.

2. **Baseline Correction**:
    Because EOG is prone to baseline drift over time, when extracting the voltage of looking at the red dot:
    * Extract the average voltage of hEOG and vEOG during `REST_START` to `REST_END` (staring at the center cross) as the baseline $V_{base\_h}, V_{base\_v}$;
    * Extract the voltage $V_h, V_v$ during `TARGET_START` to `TARGET_END` (staring at the target red dot);
    * Use relative difference for modeling: $\Delta V_h = V_h - V_{base\_h}$, $\Delta V_v = V_v - V_{base\_v}$.

3. **Data Slicing to Counteract Reaction Latency Limit**:
    * Because subjects typically need a `150ms ~ 300ms` reaction time (saccade period) after the red dot lights up for their eyeballs to actually move there.
    * **Suggested Analysis Window**: Extract the data slice from **350ms after** `TARGET_START` to `TARGET_END` as a stable period for averaging, to eliminate the interference of Reaction Time on gaze space modeling.

---

## <a id="chinese-version"></a> 中文版

# EOG 眼电网格采集系统用户手册 & 医生操作 SOP (JSON 配置版)

本系统是专为 **眼电 (EOG) 与眼球注视点同步采集** 设计的简化版双机系统。本版本实现了**软硬件参数与代码的完全解耦**，所有的网络 IP、端口、硬件卡号、通道分配、以及实验时间等可调选项均统一存放在 `config.json` 配置文件中，方便合作者在不同的实验室环境下快速调试与复部署。

---

## 一、 软硬件解耦与统一配置文件 (config.json)

项目根目录下的 [config.json](file:///%5BIP_ADDRESS%5D/config.json) 是系统唯一的配置文件。打开它即可调整以下所有参数：

```json
{
    "network": {
        "daq_pc_ip": "[IP_ADDRESS]",          // 接收端 (cDAQ PC) 的静态局域网 IP
        "udp_port": 55555                     // UDP 网络通信端口
    },
    "daq_hardware": {
        "eog_emg_dev": "cDAQ1Mod8",           // 模拟眼电/肌电卡所在 slot 槽位名称
        "eog_emg_chans": ["ai0", "ai2", "ai6"], // 启用的模拟输入通道
        "__comment_channel_mappings__": "横向电极默认ai0(hEOG)，右眼纵向电极默认ai2(vEOG_right)，左眼纵向电极默认ai6(vEOG_left)",
        "channel_mappings": {                 // 生理信号与采集卡通道映射配置
            "hEOG": "ai0",                    // 横向眼电信号
            "vEOG_right": "ai2",              // 右眼纵向眼电信号
            "vEOG_left": "ai6"                // 左眼纵向眼电信号
        },
        "sample_rate": 10000,                 // NI 卡硬件采样率 (默认 10kHz)
        "display_seconds": 5                  // cDAQ 监控端界面上显示的波形时间跨度 (秒)
    },
    "paradigm": {
        "grid_size": 5,                       // 实验刺激网格大小 (5x5 奇数网格)
        "target_show_sec": 1.0,               // 红色目标圆点点亮的时长 (秒)
        "rest_time_sec": 2.0,                 // 每次试次切换间显示中心十字(休息)的时长 (秒)
        "repeat_per_cell": 2,                 // 每个网格点位的重复采集次数
        "direction_rest_sec": 3.0,            // 切换下一个点位方向时的自动休息时间 (秒)
        "manual_confirm_direction": false     // 是否开启方向切换手动确认模式 (手动控制进度)
    }
}
```

> **💡 注意**：系统启动时若检测不到 `config.json`，将自动在根目录下创建包含上述默认参数的文件。修改此文件后无需重新编译，重新启动程序即可生效。

---

## 二、 硬件差分接线与 COM 参考端说明

为保证高精度生物电信号采集并消除共模噪声，系统采用 **差分输入 (Differential Input)** 模式：
* **通道差分接线配对**：
  * 横向眼电极通道映射为 `ai0`：物理正极接 `ai0`，物理负极接 `ai8`。
  * 右眼纵向电极通道映射为 `ai2`：物理正极接 `ai2`，物理负极接 `ai10`。
  * 左眼纵向电极通道映射为 `ai6`：物理正极接 `ai6`，物理负极接 `ai14`。
* **参考电极 (REF)**：参考电极（如耳后乳突参考点）应物理连接至 NI-9205 卡的 **`COM`** 地输入口。

---

## 三、 医生与学生的实验操作 SOP (标准操作流程)

请严格按照以下步骤开展实验，切勿颠倒顺序：

### 步骤 1：启动 cDAQ 采集软件（在 cDAQ PC 上操作）
1.  确保 NI 采集箱已通电并连接好 USB。
2.  双击运行或在命令行运行：
    ```bash
    python DAQ_GUI_server.py
    ```
3.  **检查预览**：确认窗口开启，状态栏显示 **“🟢 预览模式 (等待 CMD_START 指令)”** 且四周呈绿色。此时波形实时刷新代表正常。

### 步骤 2：启动范式主程序（在范式控制 PC 上操作）
1.  **修改配置（若有网络或时间变更）**：打开根目录下的 `config.json`，确保 `daq_pc_ip` 填入了刚才 cDAQ PC 的真实有线网卡 IP 地址。
2.  在范式电脑上运行：
    ```bash
    python 眼动范式.py
    ```
3.  **信息录入**：屏幕中央会弹出输入框，提示 **“请输入患者姓名/编号”**。
4.  输入患者标识（如 `002_李四`），点击“确定”确认。

### 步骤 3：设备联动校验
1.  输入确认后，范式电脑切入全屏黑底网格界面，显示 **“按回车键开始实验”**。
2.  **检查联动状态**：此时，范式计算机会自动发送启动信号。**请扭头检查 cDAQ PC 的屏幕**：
    *   cDAQ GUI 的状态栏必须变成 **“🔴 正在录制中 (RECORDING)”**，且窗口四周呈现**红色边框**，计时器走时。
    *   *排错*：若未变红，说明网络不通、IP 配错或防火墙拦截，请按 `ESC` 退出后排查配置再开始！

### 步骤 4：正式执行实验
1.  指导志愿者做好准备，双眼盯着屏幕。
2.  在范式电脑上按下 **“回车键”**。
3.  **志愿者任务**：
    *   当屏幕中出现**红色圆点**时，请用眼睛紧盯着红点；
    *   当红点消失、屏幕中心出现**白色十字**时，请将双眼快速移回中心十字注视。
4.  实验将依次自动遍历网格的 25 个位置（自动跳过最中央的中心格）。

### 步骤 5：实验结束与安全存盘
1.  点位遍历完成后，范式电脑会播放语音“实验完成”，并安全退出全屏。
2.  cDAQ PC 收到自动发出的停止命令，界面恢复为 **“🟢 绿色预览状态”**，并在 `EOG/` 目录下生成原始二进制数据文件 `.bin` 以及会话描述文件 `_meta.json`。
3.  范式 PC 会在 `logs/` 目录下生成本地行为 CSV 日志。

---

## 四、 实验中的键盘快捷控制

在范式 PC 上，操作人员可通过键盘随时掌控实验进度：
*   `空格键`：**暂停**实验（屏幕提示“已暂停”）。再次按 `回车键` 即可在当前位置继续实验。
*   `ESC 键`：**强行中止**实验。程序会立刻向 cDAQ 发送停止信号并安全退出，**已采集的数据段仍会安全存盘**。
*   `回车键`：在暂停状态下用于**恢复实验**，以及在手动确认模式下切换方向。

---

## 五、 针对反应迟缓/老年志愿者的参数调整指南

如果遇到反应较慢、眼球移动吃力或容易劳累的患者，请直接修改 `config.json` 中的 `paradigm` 配置：

1.  **红点注视时间不够**：
    将 `"target_show_sec"` 延长至 `1.5` 或 `2.0`（秒），让患者移过目光后，有足够长的时间（> 1秒）保持稳定注视。
2.  **切换点位疲劳/基线不稳定**：
    将 `"rest_time_sec"` 延长至 `2.5` 或 `3.0`（秒），给患者充分的时间将双眼移回中心稳定，确保基线（Baseline）彻底平稳。
3.  **患者极度容易疲劳，无法配合自动连续切换**：
    将 `"manual_confirm_direction"` 修改为 `true`。
    *   **效果**：系统在准备切换网格的每一个新点位方向前，屏幕都会呈现“准备下一个方向”，并播放语音，**此时实验无限期挂起等待**。
    *   医生可以口头指导患者闭眼休息，等患者准备妥当后，**由医生按下回车键**，范式才会开始该方向的红点呈现。这能极大地提供喘息 and 调整时间。

---

## 六、 独立双机同步时延分析工具 (analyze_sync_latency.py)

为方便在不同网速、计算机状态下评估双机 UDP 传输的延迟与抖动，系统提供了一个独立的分析脚本 [analyze_sync_latency.py](file:///c:/Users/simia/OneDrive/Data/DoCs/Tools/Simple_EOG_Paradigm/analyze_sync_latency.py)：

### 1. 实时网络延时与时钟偏差测量 (Ping-Pong)
使用 NTP 算法来检测两台电脑之间的实时单向网络延迟、RTT 抖动、以及系统时间的时钟偏差。
* **服务端 (在 DAQ PC 上执行)**：
  ```bash
  python analyze_sync_latency.py server
  ```
* **客户端 (在范式 PC 上执行)**：
  ```bash
  python analyze_sync_latency.py client --ip 10.10.10.100
  ```
  *(注：ip 参数如果不指定，程序默认从 config.json 中读取 daq_pc_ip)*
* **输出指标**：
  * RTT Jitter (标准差)：若 < 5ms，代表局域网连接非常稳定。
  * System Clock Offset：表示 DAQ 电脑与范式电脑系统时钟的毫秒级时差。

### 2. 离线日志时间戳对比 (Analyze)
利用实验结束后本地生成的行为 CSV 日志和 DAQ 保存的 `_meta.json` 里的事件打标序列进行匹配。
* **运行分析**：
  ```bash
  python analyze_sync_latency.py analyze logs/subject_眼动_20260611_120000.csv EOG/DAQ_Data_20260611_120000_meta.json -v
  ```
* **输出指标**：
  * **Mean Offset (平均时差)**：在此次实验中，由于两台 PC 时钟不一致带来的平均对齐时差。后续分析中，将 EOG 信号时间戳或者事件标记减去该 Offset 即可完全对准。
  * **Jitter (StdDev)**：网络传输与线程调度的实时抖动。若 Jitter < 10ms，说明不需要进行硬件对齐，纯 UDP 联动已足够。
  * **Clock Drift (时钟漂移)**：评估实验期间两台电脑的物理晶振频率微弱偏差带来的时钟拉长/收缩。

---

## 七、 后续建模与数据分析对齐指南 (给分析人员)

本系统使用 UDP 极简对齐方案，无需解析电平：

1.  **读取元数据**：
    打开 `EOG/` 文件夹下的 `_meta.json`，在 `events` 列表中可以读到范式 PC 广播的打标点。例如：
    ```json
    {
        "event": "T_1_R0C1_TARGET_START",
        "system_time": 17839304.51,
        "daq_sample_index": 25000
    }
    ```
    代表在 cDAQ 采集到第 `25000` 个数据点时，网格 `(Row 0, Col 1)` 位置的红点亮起。

2.  **基线漂移扣除 (Baseline Correction)**：
    由于眼电容易随时间产生基线漂移，在提取看红点段的电压时：
    *   提取 `REST_START` 至 `REST_END` 期间（注视中心十字）的 hEOG 和 vEOG 均值电压，作为基线 $V_{base\_h}, V_{base\_v}$；
    *   提取 `TARGET_START` 至 `TARGET_END` 期间（注视目标红点）的电压 $V_h, V_v$；
    *   使用相对差值进行建模：$\Delta V_h = V_h - V_{base\_h}$，$\Delta V_v = V_v - V_{base\_v}$。

3.  **数据切片抗反应延迟限制**：
    *   由于受试者在红点亮起后，通常需要 `150ms ~ 300ms` 的反应时间（扫视期）眼球才真正动过去。
    *   **建议分析窗**：提取 `TARGET_START` **后延 350ms** 至 `TARGET_END` 的数据切片作为稳定期进行平均，以剔除反应时（Reaction Time）对注视空间建模的干扰。


