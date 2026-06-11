%% Prepare_EOG_Dataset.m
% EOG 5x5 网格校准范式：多受试者数据批处理与高鲁棒性数据集构建脚本
%
% 功能说明：
%   1. 自动扫描指定的 EOG 数据目录，读取所有 *_meta.json 与对应 .bin 数据。
%   2. 将原始 10kHz 数据降采样至 250Hz，进行 0.5-15Hz 双向零相位带通滤波。
%   3. 基于 UDP 事件打标进行 Trial 分段，以注视中心十字期间为基线进行减法校正。
%   4. 对注视稳定期数据（350ms-1000ms）进行高鲁棒性眨眼检测。
%   5. 眨眼处理策略：
%      - 眨眼占比 <= 40% (短眨眼)：使用 pchip (分段三次 Hermite 插值) 进行波形修复后取均值。
%      - 眨眼占比 > 40% 或扰动过大 (长眨眼)：标记为无效 Trial 予以剔除，防止模型失真。
%   6. 将所有受试者/Session 的有效数据汇总，保存为统一的数据集文件 'Compiled_EOG_Dataset.mat'。

clear; clc; close all;

%% ========================== 1. 参数与路径配置 ==========================
% 核心路径配置（可根据实际情况修改）
script_dir = fileparts(mfilename('fullpath'));
eog_dir = fullfile(script_dir, 'EOG'); % EOG 二进制与元数据所在目录

% 屏幕与网格参数（用于计算物理像素坐标，若与实际实验不符请在此修改）
screen_width = 1920; 
screen_height = 1080;
grid_size = 5;

% 信号处理参数
fs_orig = 10000;          % 原始采集采样率
fs_new = 250;             % 目标分析采样率
low_cutoff = 0.5;         % 带通下限 (Hz)
high_cutoff = 15;         % 带通上限 (Hz)

% 稳定注视窗口参数 (相对 TARGET_START 的时间，单位：秒)
stable_start_sec = 0.35;  % 躲避扫视延迟 (反应时间)
stable_end_sec = 1.00;    % 稳定注视结束时间

% 眨眼检测参数
blink_deriv_factor = 5.0;  % 眨眼一阶差分阈值系数（几倍于整段导数的标准差）
blink_amp_factor = 4.0;    % 眨眼幅度绝对值阈值系数（几倍于整段幅度的标准差）
dilation_sec = 0.10;       % 眨眼区域向前后延展的安全时间（秒）

%% ========================== 2. 扫描数据文件 ==========================
if ~isfolder(eog_dir)
    error('未找到数据目录：%s。请检查路径配置。', eog_dir);
end

meta_files = dir(fullfile(eog_dir, '*_meta.json'));
num_files = length(meta_files);
if num_files == 0
    fprintf('未在目录 %s 中找到任何 _meta.json 文件。\n', eog_dir);
    return;
end

fprintf('共发现 %d 个数据会话 (Session)。开始批处理...\n', num_files);

% 初始化汇总表格所需的数据容器
all_subjects = {};
all_sessions = [];
all_trial_idxs = [];
all_rows = [];
all_cols = [];
all_target_x = [];
all_target_y = [];
all_delta_Vh = [];
all_delta_Vv = [];
all_blink_status = {};
all_valid = [];

% 计算网格单元的物理尺寸与坐标映射
cell_w = screen_width / grid_size;
cell_h = screen_height / grid_size;

%% ========================== 3. 循环处理每个 Session ==========================
for f_idx = 1:num_files
    meta_name = meta_files(f_idx).name;
    meta_path = fullfile(eog_dir, meta_name);
    
    bin_name = strrep(meta_name, '_meta.json', '.bin');
    bin_path = fullfile(eog_dir, bin_name);
    
    if ~isfile(bin_path)
        warning('未找到与元数据对应的二进制数据文件：%s，跳过该 Session。', bin_name);
        continue;
    end
    
    fprintf('\n--------------------------------------------------\n');
    fprintf('[Session %d/%d] 正在处理: %s\n', f_idx, num_files, meta_name);
    
    % 3.1 读取元数据 JSON
    try
        meta = jsondecode(fileread(meta_path));
    catch ME
        warning('读取元数据 JSON 失败：%s，跳过。', ME.message);
        continue;
    end
    
    % 提取受试者标识 (从元数据文件名中提取，格式一般为 DAQ_Data_YYYYMMDD_HHMMSS)
    % 也可以将 task_name 作为辅助标志
    subject_id = meta.task_name; 
    % 尝试从文件名或 task_name 提取更具体的志愿者姓名
    % 这里我们用文件名的时间戳作为 Session 的唯一标识
    session_id = f_idx; 
    
    C = length(meta.channels);
    Fs = meta.rate;
    buffer_size = meta.chunk_size;
    
    if Fs ~= fs_orig
        warning('Session 采样率 (%d Hz) 与配置的原始采样率 (%d Hz) 不符，已自动适配。', Fs, fs_orig);
        current_fs_orig = Fs;
    else
        current_fs_orig = fs_orig;
    end
    
    % 3.2 读取原始二进制数据
    fid = fopen(bin_path, 'r');
    raw = fread(fid, Inf, 'double');
    fclose(fid);
    
    % 3.3 数据维度恢复 (C-order Reshape)
    points_per_chunk = buffer_size * C;
    num_full_chunks = floor(length(raw) / points_per_chunk);
    if num_full_chunks == 0
        warning('数据长度不足以构成一个完整的块，跳过该文件。');
        continue;
    end
    raw = raw(1 : num_full_chunks * points_per_chunk);
    
    data_raw = reshape(raw, points_per_chunk, []); 
    M = reshape(data_raw, buffer_size, C, []);
    M = permute(M, [1, 3, 2]); 
    final_data = reshape(M, [], C); % 维度: [TotalSamples, Channels]
    
    % 3.4 提取 hEOG 和 vEOG
    h_chan = 'ai0';
    v_chan = 'ai2';
    if isfield(meta, 'channel_mappings')
        if isfield(meta.channel_mappings, 'hEOG')
            h_chan = meta.channel_mappings.hEOG;
        end
        if isfield(meta.channel_mappings, 'vEOG_right')
            v_chan = meta.channel_mappings.vEOG_right;
        end
    end
    
    h_idx = find(contains(meta.channels, ['/' h_chan]));
    v_idx = find(contains(meta.channels, ['/' v_chan]));
    
    if isempty(h_idx) || isempty(v_idx)
        error('未能在通道列表中找到 hEOG (/%s) 或 vEOG (/%s)。', h_chan, v_chan);
    end
    
    hEOG_raw = final_data(:, h_idx);
    vEOG_raw = final_data(:, v_idx);
    
    % 3.5 降采样与零相位滤波
    downsample_factor = current_fs_orig / fs_new;
    fprintf('   - 正在进行降采样: %d Hz -> %d Hz...\n', current_fs_orig, fs_new);
    hEOG_ds = resample(hEOG_raw, 1, downsample_factor);
    vEOG_ds = resample(vEOG_raw, 1, downsample_factor);
    
    fprintf('   - 正在进行双向零相位滤波 (%0.1f - %d Hz)...\n', low_cutoff, high_cutoff);
    [b, a] = butter(4, [low_cutoff, high_cutoff] / (fs_new/2), 'bandpass');
    hEOG_filt = filtfilt(b, a, hEOG_ds);
    vEOG_filt = filtfilt(b, a, vEOG_ds);
    
    % 计算全段的差分及标准差，供后续眨眼检测做基线阈值
    session_dv_std = std(diff(vEOG_filt));
    session_v_std = std(vEOG_filt);
    
    % 3.6 事件打标解析与 Trial 分类整理
    if ~isfield(meta, 'events') || isempty(meta.events)
        warning('未在元数据中找到事件打标 (events)，跳过该 Session。');
        continue;
    end
    
    num_events = length(meta.events);
    trials = struct('row', {}, 'col', {}, 'trial_idx', {}, ...
                    'rest_start', {}, 'rest_end', {}, ...
                    'target_start', {}, 'target_end', {});
    trial_map = containers.Map();
    
    for k = 1:num_events
        ev = meta.events(k);
        % 解析打标名称。格式: T_{trial_idx}_R{row}C{col}_{type}
        tokens = regexp(ev.event, 'T_(\d+)_R(\d+)C(\d+)_(REST_START|REST_END|TARGET_START|TARGET_END)', 'tokens');
        if isempty(tokens)
            continue;
        end
        
        t_idx = str2double(tokens{1}{1});
        r = str2double(tokens{1}{2});
        c = str2double(tokens{1}{3});
        type = tokens{1}{4};
        
        key = sprintf('R%dC%dT%d', r, c, t_idx);
        
        if ~isKey(trial_map, key)
            new_idx = length(trials) + 1;
            trials(new_idx).row = r;
            trials(new_idx).col = c;
            trials(new_idx).trial_idx = t_idx;
            trials(new_idx).rest_start = [];
            trials(new_idx).rest_end = [];
            trials(new_idx).target_start = [];
            trials(new_idx).target_end = [];
            trial_map(key) = new_idx;
        end
        
        idx = trial_map(key);
        % 将原始采样率下的 index 转换到降采样后的 index
        ds_idx = round(ev.daq_sample_index / downsample_factor);
        if ds_idx <= 0, ds_idx = 1; end
        
        switch type
            case 'REST_START'
                trials(idx).rest_start = ds_idx;
            case 'REST_END'
                trials(idx).rest_end = ds_idx;
            case 'TARGET_START'
                trials(idx).target_start = ds_idx;
            case 'TARGET_END'
                trials(idx).target_end = ds_idx;
        end
    end
    
    fprintf('   - 成功解析出 %d 个 Gaze Trials，开始分段与眨眼伪迹处理...\n', length(trials));
    
    % 3.7 Trial 分段处理与高鲁棒性特征提取
    valid_trial_count = 0;
    for t_idx = 1:length(trials)
        tr = trials(t_idx);
        
        % 边界安全检查
        if isempty(tr.rest_start) || isempty(tr.rest_end) || ...
           isempty(tr.target_start) || isempty(tr.target_end)
            continue; % 事件不完整，丢弃
        end
        if tr.target_end > length(vEOG_filt)
            continue; % 数据截断，丢弃
        end
        
        % 3.7.1 基线计算 (凝视中心十字 REST 期间)
        % 截取稳定基线区：去除前 200ms 和后 100ms 躲避视线移动过渡期
        rest_trim_start = tr.rest_start + round(0.20 * fs_new);
        rest_trim_end = tr.rest_end - round(0.10 * fs_new);
        
        if rest_trim_start >= rest_trim_end
            rest_trim_start = tr.rest_start;
            rest_trim_end = tr.rest_end;
        end
        
        v_base = mean(vEOG_filt(rest_trim_start:rest_trim_end));
        h_base = mean(hEOG_filt(rest_trim_start:rest_trim_end));
        
        % 3.7.2 稳定注视期切片 (TARGET_START 延后 350ms 至结束)
        stable_start = tr.target_start + round(stable_start_sec * fs_new);
        stable_end = tr.target_end;
        
        if stable_start >= stable_end
            continue; 
        end
        
        v_segment = vEOG_filt(stable_start:stable_end);
        h_segment = hEOG_filt(stable_start:stable_end);
        seg_len = length(v_segment);
        
        % 3.7.3 眨眼检测 (Blink Detection)
        % 联合一阶导数突变和电压绝对值判断
        dv = abs([0; diff(v_segment)]);
        is_blink = (dv > (blink_deriv_factor * session_dv_std)) | ...
                   (abs(v_segment) > (blink_amp_factor * session_v_std));
        
        % 对检测到的眨眼标志点向前后各延时膨胀 (Dilation)
        dilation_pts = round(dilation_sec * fs_new);
        blink_indices = find(is_blink);
        for b_i = 1:length(blink_indices)
            p_start = max(1, blink_indices(b_i) - dilation_pts);
            p_end = min(seg_len, blink_indices(b_i) + dilation_pts);
            is_blink(p_start:p_end) = true;
        end
        
        blink_ratio = sum(is_blink) / seg_len;
        
        % 3.7.4 眨眼修复与评估决策 (Blink Repair & Rejection Decision)
        is_valid = true;
        blink_status = 'None';
        
        if blink_ratio == 0
            % 无眨眼，常规求均值
            v_val = mean(v_segment);
            h_val = mean(h_segment);
        elseif blink_ratio > 0 && blink_ratio <= 0.40
            % 短时间眨眼 (占比 <= 40%)，利用 pchip (分段三次 Hermite 多项式) 进行插值修复
            non_blink_pts = find(~is_blink);
            blink_pts = find(is_blink);
            
            if length(non_blink_pts) >= 8 % 确保有足够的无干扰采样点作为拟合边界
                v_segment_repaired = v_segment;
                h_segment_repaired = h_segment;
                
                % 对 vEOG 与 hEOG 进行插值修复
                v_segment_repaired(blink_pts) = interp1(non_blink_pts, v_segment(non_blink_pts), blink_pts, 'pchip');
                h_segment_repaired(blink_pts) = interp1(non_blink_pts, h_segment(non_blink_pts), blink_pts, 'pchip');
                
                v_val = mean(v_segment_repaired);
                h_val = mean(h_segment_repaired);
                blink_status = sprintf('Repaired (Blink ratio: %.1f%%)', blink_ratio*100);
            else
                % 无效点过多，无法插值，强制剔除
                v_val = NaN;
                h_val = NaN;
                is_valid = false;
                blink_status = 'Rejected (Too few points for interp)';
            end
        else
            % 长时间眨眼/眼动扰动过大 (占比 > 40%)，直接剔除该 Trial
            v_val = NaN;
            h_val = NaN;
            is_valid = false;
            blink_status = sprintf('Rejected (Blink ratio: %.1f%%)', blink_ratio*100);
        end
        
        % 3.7.5 计算基线扣除的电压变化量
        if is_valid
            delta_Vh = h_val - h_base;
            delta_Vv = v_val - v_base;
            valid_trial_count = valid_trial_count + 1;
        else
            delta_Vh = NaN;
            delta_Vv = NaN;
        end
        
        % 计算该 Trial 红点的物理像素坐标 (中心坐标对应 Row=2, Col=2)
        target_x = tr.col * cell_w + cell_w / 2;
        target_y = tr.row * cell_h + cell_h / 2;
        
        % 保存记录
        all_subjects{end+1, 1} = subject_id;
        all_sessions(end+1, 1) = session_id;
        all_trial_idxs(end+1, 1) = tr.trial_idx;
        all_rows(end+1, 1) = tr.row;
        all_cols(end+1, 1) = tr.col;
        all_target_x(end+1, 1) = target_x;
        all_target_y(end+1, 1) = target_y;
        all_delta_Vh(end+1, 1) = delta_Vh;
        all_delta_Vv(end+1, 1) = delta_Vv;
        all_blink_status{end+1, 1} = blink_status;
        all_valid(end+1, 1) = is_valid;
    end
    
    fprintf('   - Session %d 完成。总计解析: %d 个 Trials，有效: %d，剔除: %d。\n', ...
            f_idx, length(trials), valid_trial_count, length(trials) - valid_trial_count);
end

%% ========================== 4. 数据汇总与数据集导出 ==========================
fprintf('\n==================================================\n');
fprintf('数据汇总与最终数据集导出...\n');

% 将结果汇总为 MATLAB Table 方便查看与管理
EOG_Dataset = table(all_subjects, all_sessions, all_trial_idxs, all_rows, all_cols, ...
                    all_target_x, all_target_y, all_delta_Vh, all_delta_Vv, ...
                    all_blink_status, all_valid, ...
                    'VariableNames', {'SubjectID', 'SessionID', 'TrialIdx', 'Row', 'Col', ...
                                      'TargetX', 'TargetY', 'delta_Vh', 'delta_Vv', ...
                                      'BlinkStatus', 'IsValid'});

% 过滤出用于回归建模的纯净训练数据子集 (IsValid == 1)
clean_data_mask = EOG_Dataset.IsValid == 1;
clean_dataset = EOG_Dataset(clean_data_mask, :);

% 提取特征矩阵与标签矩阵
X_train = [clean_dataset.delta_Vh, clean_dataset.delta_Vv]; % 输入特征 (H, V 电压)
Y_train = [clean_dataset.TargetX, clean_dataset.TargetY];   % 目标标定 (X, Y 像素)

% 导出为 MAT 文件
output_mat_path = fullfile(script_dir, 'Compiled_EOG_Dataset.mat');
save(output_mat_path, 'EOG_Dataset', 'X_train', 'Y_train');

fprintf('数据集创建成功！\n');
fprintf('   - 导出路径: %s\n', output_mat_path);
fprintf('   - 总汇总 Trial 数: %d\n', height(EOG_Dataset));
fprintf('   - 用于建模的有效 Trial 数 (已去除严重眨眼/伪迹): %d\n', height(clean_dataset));
fprintf('   - 被修复并保留的轻微眨眼 Trial 数: %d\n', sum(contains(clean_dataset.BlinkStatus, 'Repaired')));
fprintf('\n您可以在 MATLAB 中直接 load 此文件，使用 X_train 和 Y_train 运行多项式拟合或机器学习回归建模！\n');
