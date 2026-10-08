# 脚本、结构与最小执行

## 边界

Python 3.9+标准库；探测/渲染/抽帧另需现成FFmpeg与ffprobe，基线字幕需libass。不要为运行示例自动安装依赖。字体可由系统fontconfig寻找，或设项目内 font_file；实际字体哈希进入缓存。字体须获使用授权，检查目标语言是否缺字。

基线支持一段一个连续真实口播、一个可选录屏clip或freeze、矩形人物窗、保持比例留边、独立字幕带、回看/省略标签。保留原声音量，不自动识别/去提示/降噪/调色/归一化，不支持独立外录音、动态窗位、圆形蒙版、复杂B-roll或静态无声尾卡。需要这些时用原工程/编辑器或可用Remotion实现，仍遵守素材、时间线和验收契约；不要用伪口播或超长冻结凑出不支持的镜头。

脚本不会确认语义、听感、人物贡献或用户意见。空工程拒绝编译；渲染不会覆盖已存在MP4；machine检查不会把人工层设passed。`init`只创建全新目录，返修不得运行它。

## 初次制作

`$SKILL`指本技能目录，`$PROJECT`指新建工程目录。以下命令由任务环境执行，路径按实际填写。

```bash
python3 "$SKILL/scripts/project.py" init "$PROJECT" --id demo
python3 "$SKILL/scripts/project.py" ingest "$PROJECT/project.json" /path/to/talk.mp4 --id talk01 --role talk
python3 "$SKILL/scripts/project.py" ingest "$PROJECT/project.json" /path/to/screen.mp4 --id screen01 --role screen
```

填写 brief.md、来源 provenance/authorization 与实际检查状态；在 project.json 添加镜头。先实际审听/看源，不能直接复制下例声称它来自用户。

```json
{
  "id": "step01",
  "chapter": "选中选项",
  "talk": {"source": "talk01", "start": 2.0, "end": 7.0},
  "spoken_text": "这里填实际收到的完整口述。",
  "cues": [{"start": 0.0, "end": 5.0, "text": "这里填核对后的字幕。"}],
  "screen": {"source": "screen01", "mode": "clip", "start": 10.0, "end": 15.0},
  "evidence": {
    "object": "准确对象/题目ID",
    "before": "未选择",
    "after": "真实选中项；尚未出现奖励",
    "sync_method": "静音录屏，按可见点击及语义配对",
    "source_note": "来源与人工核对记录"
  },
  "annotation": "",
  "replay_label": "",
  "omitted_steps": false,
  "omission_label": ""
}
```

- `talk`同时定义人物和原声区间，不接受分离的音频区间。时间均为秒、源时间半开区间 `[start,end)`；编译后按输出fps量化，最多半帧差异，末端可补不足一帧/等长音频。禁止声称这就是独立声画同步检测。
- 没有screen时展示完整口播；clip须有足够真实区间，不能默默延长。freeze只用start定位，必须有可见replay_label，如“画面回看 · 已选中选项”；不写end，标签会烧录进画面。基线选择不早于start的第一张实际源帧，将请求/实际时间写入渲染清单；近片尾无后继帧时明确拒绝，改用已核对的更早时间，不能默默回退。
- `cues`是段内剪后秒数，非原始源时间；必须非重叠且不超段。省略cues可用subtitle，或默认spoken_text整段显示。长句需拆cues；不能依赖自动换行保证可读性。
- `annotation`是画面整理说明，不属于口述字幕；`omitted_steps=true`时必须有可见omission_label。
- 文件路径限项目内部，迁移时带媒体或按来源清单重新连接；sources储存完整ffprobe及SHA256。新源内容使用新ID，避免静默替换受检原片。
- `assets`登记外部渲染依赖 `{path,sha256}`。基线仅支持纯色背景，不声称自定义图片/蒙版已生效；在扩展引擎使用时把所有依赖纳入缓存。
- 画幅/字体/PiP为可改配置，模板尺寸只是技术起点，不表示用户风格。PiP不能出界或压字幕带。基线fit/letterbox，不自动裁脸。

## 编译、导出与回执

```bash
python3 "$SKILL/scripts/project.py" compile "$PROJECT/project.json"
python3 "$SKILL/scripts/render.py" "$PROJECT/project.json" --out "$PROJECT/out/final/demo-v1.mp4"
python3 "$SKILL/scripts/qa.py" machine "$PROJECT/out/final/demo-v1.mp4" --version v1 --timeline "$PROJECT/analysis/timeline.json" --out "$PROJECT/out/qa/v1-receipt.json"
python3 "$SKILL/scripts/qa.py" frames "$PROJECT/out/final/demo-v1.mp4" --times 0.2 1.2 --out "$PROJECT/out/qa/v1-frames"
python3 "$SKILL/scripts/project.py" review-page "$PROJECT/project.json" --video "$PROJECT/out/final/demo-v1.mp4" --receipt "$PROJECT/out/qa/v1-receipt.json" --out "$PROJECT/review-v1.html"
```

frames的时间点应覆盖实际重要场景，本例不是通用抽样计划。machine返回非零或 failed时应修复；verify发现stale时旧回执不得使用。review-page检查文件存在及视频指纹，仍须用实际浏览器核查播放器、链接、章节和存储。delivery.files可加入预览和工程包，格式 `{label,path}`，文件必须真实存在。页面把配套资料复制到内容寻址的.review-snapshots目录并固定链接，不会自动复制sources原媒体；别把大原片列入delivery.files，除非确实要求携带。分享本地审片入口时连同此目录一起交付。归档project.json要恢复编辑，应复制回原工程根目录。

只有实际执行对应审查后才登记，例如视觉抽查的方法、时间点、证据位置。full_human_listen_review通过必须 scope=full 且有完整真实听音记录；脚本无法替代判断。

```bash
python3 "$SKILL/scripts/qa.py" record RECEIPT FINAL_MP4 --kind visual_sample_review --status partial --reviewer REVIEWER --method METHOD --scope TIMECODES --evidence EVIDENCE
python3 "$SKILL/scripts/qa.py" verify RECEIPT FINAL_MP4
```

## 返修

复制 project.json 到 versions/版本.json，再改原project.json的稳定镜头ID与version。编译将同步覆盖派生timeline/SRT/CSV，旧版应先另存；输出新MP4和新回执。缓存可复用未改镜头，但修改字幕、参数、源/依赖内容、脚本或字体会换键。`cache-key PROJECT SEGMENT_ID`供诊断；渲染实际键额外绑定FFmpeg和所选字体。

基线concat接缝与音频需实际检查，不能只看段落各自正常。用户要求静态尾卡或声音精修时使用适合的项目引擎；先做短样段验证后再整片。
