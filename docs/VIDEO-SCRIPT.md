# 演示视频台词（3 分钟）

**硬性要求**：时长 < 3:00，上传 YouTube 且**公开**。

本脚本旁白 **395 词**（已实测）。按语速换算：

| 语速 | 时长 |
|---|---|
| 140 wpm（偏慢） | 2:49 |
| 145 wpm（目标） | 2:43 |
| 150 wpm（偏快） | 2:38 |

时间轴按 145 wpm 标注，合计 **2:48**——比纯语速多出 5 秒，是留给界面切换和转圈的。
**念到 140 wpm 也还在 3:00 以内**，但缓冲只剩 11 秒，所以如果试念后偏慢，直接按文末「超时砍句清单」删三句。

**旁白用英文**。提交材料要求英文；中文旁白必须配烧进画面的英文字幕，风险更高。
每句下面附中文对照，是给你确认「自己念的是什么」，不要念中文。

---

## 录制前 10 分钟检查清单（顺序别换）

| # | 事项 | 为什么 |
|---|---|---|
| 1 | Google Console 把 consent screen **Publish app**，然后**重新授权** | Testing 状态下 refresh token 7 天过期，评审是 12-01~12-15，现在不发布到时候 demo 必挂 |
| 2 | 删掉 Gmail 里 3 个自测草稿（`InboxPilot self test` / `idempotency check` / `acceptance check`） | 评委看到 "self test" 会以为项目没做完 |
| 3 | 后端用 `python scripts/serve.py --no-reload` 重启 | 默认开 reload，录屏中途重启会闪；且必须跑当前代码 |
| 4 | `My week` 页换成**你自己的真实课表** | 数据库里现在是我放的示例课表（周二/周四 9-11） |
| 5 | 浏览器缩放 125%，隐藏书签栏，关掉其他标签页和系统通知 | 1080p 下 100% 缩放的字在手机上完全看不清 |
| 6 | **先在真实屏幕上跑一遍 watcher，确认卡片看得见** | 这台机器截不到桌面（另一个 window station），卡片视觉效果只能你亲眼确认 |
| 7 | 留一封**没分析过**的邮件当现场演示，其余提前分析好 | 现场要看到真实的 5 秒模型调用，但不能每次都等 |

---

## 分镜与台词

### 场景 1 ｜ 0:00 – 0:16 ｜ 问题

**画面**：停在收件箱列表，光标不动。不要动，让观众先看清这是真邮箱。

> Email assistants all stop at the same place: a summary. But a summary is just
> a second inbox you have to read. The hard part was never reading — it's
> deciding. So InboxPilot reads, decides, and then, deliberately, stops and asks.

*中文对照：邮件助手都停在同一个地方——一份摘要。但摘要只是第二个你必须读的收件箱。难的从来不是读，是决定。所以 InboxPilot 读、决定，然后刻意停下来问你。*

**这一句是全片论点**，"deliberately" 要重读。不要念快。

---

### 场景 2 ｜ 0:16 – 0:28 ｜ 这是什么

**画面**：鼠标划顶栏六个页面（Connect / Inbox / Digest / Analysis / My week / Action Review），停在 Inbox。

> It runs on a real Gmail account. FastAPI backend, Streamlit frontend, and
> every answer is a live call to Nebius Token Factory. Nothing pre-recorded.
> Nothing faked.

*中文对照：它跑在真实的 Gmail 账号上。FastAPI 后端、Streamlit 前端，每个答案都是对 Nebius Token Factory 的实时调用。没有预录，没有造假。*

---

### 场景 3 ｜ 0:28 – 0:44 ｜ 收件箱

**画面**：点 `Inbox`，十封邮件加载。注意**已经有分析的邮件直接显示分类徽章和「6 key facts · 0 action items」**——那是后台 watcher 提前读过的，打开就是结果，不用再点。鼠标移到某封的 `Analyze` 按钮上（不要点），然后点下去。转圈。

> Here's the inbox — ten messages, each with its own Analyze button, so I can do
> one or ten without leaving the list. This one is from a classmate, and it has
> a deadline in the third paragraph.

*中文对照：这是收件箱——十封邮件，每封都有自己的 Analyze 按钮，不用离开列表就能分析一封或十封。这封来自同学，截止日期藏在第三段。*

**转圈期间继续念，不要停顿。** 这是真实延迟，别剪掉，评委看得出真伪。

---

### 场景 4 ｜ 0:44 – 1:04 ｜ 证据（核心技术差异化）

**画面**：分析**就在这一行下面展开**，不需要滚到别处——summary 面板 → key facts（每条带引文和 `explicit` 徽章）→ action items 卡片。鼠标在**引文**上停留。这一段停留久一点，是全片技术差异化的核心。

> Every claim carries a quote from the email and a label: explicit, inference,
> or unknown. Then the backend checks that each explicit quote actually appears
> in the body. If the model invented it, it gets downgraded or dropped. The
> model isn't trusted — it's audited.

*中文对照：每条结论都带一句邮件原文引用和一个标签：explicit（明说）、inference（推断）、unknown（不确定）。后端会校验每句 explicit 引用是否真的出现在正文里。模型编的会被降级或丢掉。我们不信任模型——我们审计它。*

**"audited" 是全片第二个记忆点**，重读。

> 收尾前可以顺手点一下 `Hide analysis` 把它收起来，让镜头停在「摘要、facts 数、action items 数」那一行——这一下能同时说明「分析挂在邮件上」和「可以收起」。不加台词，纯动作，约 1 秒。

---

### 场景 5 ｜ 1:04 – 1:34 ｜ 周表对照（别的邮件 agent 没有）

**画面**：切 `My week`，展示课表编辑器和阈值滑杆（不要停留太久）→ 切回 `Analysis`，滚到 `Should I attend?` 结论卡。

> Now the part I care most about. I told it my week once — lectures, a lab, a
> shift — and set a bar for what an event has to be worth before I skip
> something for it. So it answers the real question: does this fit my week? The
> model reads when the event is. Python does the arithmetic. A lecture ending at
> eleven does not clash with an event starting at eleven.

*中文对照：现在是我最在意的部分。我一次性告诉它我这周的安排——课、实验、排班——并设了一条线：一个活动值多少，我才愿意为它调开别的事。所以它回答真正的问题：这跟我的周表冲突吗？模型只读「活动在什么时候」，算术交给 Python。十一点结束的课，和十一点开始的活动，不冲突。*

**"A lecture ending at eleven does not clash with an event starting at eleven"** 是整片最具体的一句，说完停半秒。

---

### 场景 6 ｜ 1:34 – 1:56 ｜ 批准 → 草稿 → 回读验证

**画面**：`Analysis` 页底部点 `Plan a reply` → 提案出现（能看到 "still unknown" 那部分）→ 点 `Approve` → 成功卡出现 `verified: true` + draft id。

> When it proposes a reply, it tells me what it still doesn't know — and it will
> not send. Approving creates a Gmail draft. Then it reads that draft back from
> Gmail and compares subject, recipient and body. There is no send call anywhere
> in this codebase.

*中文对照：它提议回复时，会告诉我它还不知道什么——而且它不会发送。批准之后创建的是 Gmail 草稿。然后它把草稿从 Gmail 读回来，逐项比对主题、收件人和正文。这个代码库里没有任何一处发送调用。*

**如果录屏里 draft id 看不清，就在这一句上放大或加一个方框标注。**

---

### 场景 7 ｜ 1:56 – 2:16 ｜ 主动工作 + 周报

**画面**：终端里 watcher 跑着 → 桌面角落卡片弹出（**这一段只能你自己录**，我这边截不到桌面）→ 切 `Digest` 页。

> It also works before I ask. A watcher polls the inbox, analyses what arrives,
> and puts a card in the corner of the screen — outside the browser. It can
> read, and it can notify. It cannot draft, label, or approve anything. And the
> Digest page reduces a whole week to one page.

*中文对照：它也会在你开口之前工作。watcher 轮询收件箱，分析新到的邮件，在屏幕角落弹一张卡片——在浏览器之外。它能读，能通知。它不能建草稿、不能改标签、不能花掉一次批准。Digest 页把一整周压成一页。*

**"It cannot draft, label, or approve anything" 是安全叙事的关键**，语速放慢。

---

### 场景 8 ｜ 2:16 – 2:36 ｜ Nebius / Nemotron（评委打分项）

**画面**：这里建议插一张**纯文字卡**（黑底白字即可），写：

```
nvidia/nemotron-3-super-120b-a12b   →  read one email
nvidia/Nemotron-3-Ultra-550b-a55b   →  decide + draft
nvidia/Nemotron-3.5-Super-VL        →  read a timetable photo

thinking off: 2793 ms → 1088 ms   (2.6× faster, 42% fewer tokens)
```

> Three Nemotron models on Nebius: one reads each email, the reasoning model
> decides and drafts, and a vision model reads a photo of my timetable. The
> biggest speed win came from measurement — thinking was on by default, and
> turning it off for extraction made it two point six times faster.

*中文对照：三个 Nemotron 模型跑在 Nebius 上：一个读每封邮件，推理模型负责决策和起草，一个视觉模型读我课表的照片。最大的一次提速来自测量——思考默认是开的，关掉它之后，抽取快了 2.6 倍。*

**这一段必须念，不能只放文字。** 赛道要求说明 Nebius / Nemotron 用在哪里。

---

### 场景 9 ｜ 2:36 – 2:48 ｜ 收尾

**画面**：回到 Inbox 页，或停在 GitHub 仓库首页。

> InboxPilot: from email information to real-world action, with a human on the
> gate. Code, tests and setup are in the repository. Two hundred and ninety-two
> tests, all passing.

*中文对照：InboxPilot——从邮件信息到真实行动，闸门后面始终有个人。代码、测试和安装步骤都在仓库里。292 个测试，全过。*

---

## 录制方法

1. **先录旁白，再录画面。** 用手机或 Audacity 把上面 9 段一次念完，每段之间留 2 秒空白。然后放音轨、对着音轨录屏，卡时间最省事。
2. 录屏用 OBS：1920×1080、30fps、显示鼠标指针。或者 PowerPoint 的录制功能也够。
3. 念稿速度 **145 词/分钟**——比日常说话慢一点，但比"演讲腔"自然。先念场景 1 试一次，用秒表卡：40 词应该在 **16–17 秒**之间念完。偏快就整体放慢。
4. **不要加速画面**来掩盖等待。真实的 5 秒模型调用配上旁白反而显得可信；把 spinner 剪掉或加速，评委一眼看得出。

## 超时砍句清单（按顺序砍，砍到 3:00 以内就停）

如果实际念出来超过 3 分钟，按这个顺序删，**每一条都不伤主线**：

1. 场景 3：`so I can do one or ten without leaving the list` → 省 11 词（约 4.5 秒）
2. 场景 5：`— lectures, a lab, a shift —` → 省 6 词（约 2.5 秒）
3. 场景 8：`and a vision model reads a photo of my timetable` → 省 9 词（约 3.7 秒）

三条全砍省约 11 秒，落到 2:32。

**绝对不要砍**：场景 1 的 `stops and asks`、场景 4 的 `audited`、场景 6 的 `no send call`、场景 7 的 `cannot draft, label, or approve`、场景 8 的 `2.6 times faster`。这五处是评分点。

## 上传

- YouTube 标题：`InboxPilot — from email information to real-world action (Nebius x NVIDIA Hackathon)`
- 可见性：**Public**（Unlisted 不算，规则要求公开）
- 描述里放 GitHub 链接
- 上传后**自己用手机无痕模式点开确认能播**，再填 Devpost
