# 演示邮件（7 封）

录制前用来填充收件箱的素材。按你**真实的 UM 课表**设计，日期都算过。

---

## 先说为什么不由我直接发

我查了代码，两个硬阻碍：

**1. 你自己发给自己的邮件会被 watcher 过滤掉。**
`backend/services/watcher.py` 第 82 行 `is_noise()` 里有一句
`if account and address == account.strip().lower(): return True`，docstring 原文是
「Mail the user sent to themselves is caught by address **so the demo does not
announce its own test messages back at the presenter**」。

后果：这些邮件在 Inbox / Analysis / Digest 都正常，但 **watcher 一张卡片都不弹**——
正好是台词场景 7 要录的东西。而且 7 封全来自同一个地址，镜头里一眼就看出是造假，
和台词里 "Nothing faked" 直接冲突。

**2. 项目本身没有发送能力，这是卖点。**
`gmail.compose` 技术上能 `messages.send`，但产品代码里一处发送调用都没有——
这是你 README 和 Devpost 故事里的核心安全声明。为了造素材在仓库里加一条发送路径，
等于自己拆自己的台。

## 所以：从你的**学生邮箱**发

`24211639@siswa.um.edu.my` —— 你本来就有，域名就是大学，发到 `flare8363@gmail.com`
看起来完全是真实的校内邮件。**不用新建账号。**

> 顺手改一下学生邮箱的显示名（Gmail → 设置 → 账号 → 姓名），改成
> `FSKTM Academic Office` 或你的真名。1 分钟，让发件人看起来对。

---

## 你的真实课表（用来判断冲突）

| 星期 | 时间段 |
|---|---|
| 周一 | 10:00–12:00, 12:00–14:00, 14:00–16:00 |
| 周二 | 08:00–10:00, 12:00–14:00, 16:00–18:00 |
| 周三 | 08:00–09:00, 15:00–16:00, 17:00–19:00 |
| 周四 | 09:00–10:00, 11:00–12:00, 12:00–14:00, 15:00–16:00 |
| 周五六日 | 空 |

阈值 `value_threshold = 4`。

### 冲突已用真实代码验证过（不是手算）

跑 `schedule.find_conflicts()` 对三个活动时间求交，实际输出：

```
NVIDIA Challenge: 3 conflict(s)
    - Mon 2026-11-02 10:00-12:00 (WIA2002 - SOFTWARE MODELLING | LECTURE)
    - Mon 2026-11-02 12:00-14:00 (WIA2007 - MOBILE APPLICATION DEVELOPMENT | TUTORIAL)
    - Mon 2026-11-02 14:00-16:00 (WIA2003 - PROBABILITY AND STATISTICS | LECTURE)
GDG KL: 0 conflict(s)
Resume webinar: 1 conflict(s)
    - Thu 2026-11-05 09:00-10:00 (WIA2002 - SOFTWARE MODELLING | TUTORIAL)
```

配合 `triage.decide()` 的判定表（冲突且 ≥4 → Go anyway；冲突且 <4 → Decline；
无冲突且 ≥3 → Go；无冲突且 <3 → Skip），三封的结论是确定的——
只有**价值分**是模型给的，那个才有浮动空间。

---

## 邮件 1 ｜冲突 + 高价值 → 预期「Go anyway」

**Subject**
```
NVIDIA AI Student Challenge — regional round, 2 November
```

**Body**
```
Dear students,

The Faculty has received an invitation from NVIDIA for the AI Student Challenge
regional round, which will be held on Monday, 2 November 2026 from 10:00 to
16:00 at the Asia Pacific University auditorium.

Teams of up to four will build an application using NVIDIA Nemotron models
served through Nebius Token Factory. The top three teams receive an NVIDIA
Jetson Orin kit, and the winning team is invited to the regional final in
Singapore in January 2027.

Lunch and transport reimbursement will be provided. Registration closes on
30 October 2026. Bring your student ID and a laptop.

We would be glad to see FSKTM represented.

Regards,
Dr. Tan Wei Ming
Academic Office
```

**为什么这么设计**：周一是 10:00–16:00 三段课全占（WIA2002 / WIA2007 / WIA2003），
冲突拉满；但 Jetson + 新加坡决赛 + 差旅报销 → 价值应打到 5，越过阈值 4 →
**Go anyway**。注册今天截止，同时能触发 urgency 3。

---

## 邮件 2 ｜不冲突 + 值得去 → 预期「Go」

**Subject**
```
GDG Kuala Lumpur — building with Gemini, Saturday 7 November
```

**Body**
```
Hi everyone,

Forwarding this in case anyone is free. GDG Kuala Lumpur is running a meetup on
Saturday, 7 November 2026, 14:00-17:00 at the Google Malaysia office in KL
Sentral (Level 12, Menara Southpoint).

Three talks: retrieval-augmented generation in production, how to evaluate LLM
output quality, and a hands-on lab where you deploy a small agent end to end.
Bring a laptop if you want to do the lab.

Free, but seats are limited to 80. RSVP by 5 November.

Aina
```

**为什么**：11 月 7 日是周六，课表全空 → 无冲突；三个 talk 有具体内容、
有实操 → 价值 4 → **Go**。

---

## 邮件 3 ｜冲突 + 低价值 → 预期「Decline」

**Subject**
```
Free webinar: ten tips for a winning resume
```

**Body**
```
Dear students,

Join our free online webinar on Thursday, 5 November 2026, 09:00-10:30 to learn
ten tips for writing a winning resume. Our career coach will cover formatting,
keywords, and the most common mistakes we see in student applications.

The session is conducted over Zoom. A recording will be available afterwards
for everyone who registers, so you do not need to attend live.

Register now to secure your place.

Career Services Team
```

**为什么**：周四 09:00–10:30 撞 WIA2002 tutorial（09:00–10:00）；但内容泛泛、
且明说「有录像不用到场」→ 价值 2 < 阈值 4 → **Decline**。
「有录像」这句是故意写的，它让「不去」变成理性选择而不是懒惰。

---

## 邮件 4 ｜明天截止 → 预期 urgency 3「Due tomorrow」

**Subject**
```
WIA2007 project proposal — submission reminder
```

**Body**
```
Dear students,

This is a reminder that the project proposal for WIA2007 Mobile Application
Development is due on Saturday, 31 October 2026 at 23:59. Submit through
Spectrum before the deadline; late submissions incur a 10% penalty per day.

The proposal should be five slides: problem statement, target users, proposed
features, technology stack, and a one-week milestone plan. One submission per
group, and every member's name and matric number must appear on the cover slide.

Dr. Norhayati
Department of Software Engineering
```

**为什么**：31 日是**明天** → `urgency()` 判定 `min(deadlines) <= today + 1 day`
→ 3 级 → 卡片写 **"Due tomorrow (Oct 31)"**。这是 watcher 演示最好用的一封。

---

## 邮件 5 ｜纯信息，无行动 → 预期 urgency 1（分析但**不弹卡**）

**Subject**
```
Library extended hours during the final examination period
```

**Body**
```
Dear students,

From 1 November to 12 December 2026, the main library will remain open until
02:00 on weekdays and 24 hours on weekends to support students during the final
examination period.

Group discussion rooms can be booked up to three days in advance through the
library portal. The 24-hour reading room on Level 3 requires your student card
for entry after 22:00.

Please note that food and drinks are still not permitted in the reading areas.

Library Services
```

**为什么**：完全没有待办 → 分析后缓存，**不弹卡**。这正是「默认沉默」的证明：
watcher 不是什么都通知。

---

## 邮件 6 ｜垃圾邮件 → 预期分类为 promotion / spam

**Subject**
```
Congratulations! You have been selected for a RM500 shopping voucher
```

**Body**
```
Dear Valued Customer,

You have been selected to receive a RM500 shopping voucher as part of our
Loyalty Reward Programme 2026!

To claim your reward, simply click the link below and complete a short
two-minute survey. This offer is valid for 48 hours only and will expire on
1 November 2026.

Do not miss this exclusive opportunity. Thousands of customers have already
claimed their rewards.

Claim now: [link removed]

Customer Rewards Team
```

---

## 邮件 7 ｜群发资讯 → 预期被归入 ignored

**Subject**
```
This Week in AI — Issue #47
```

**Body**
```
Your weekly digest of AI news.

1. Open-weight models close the gap on reasoning benchmarks
2. A new benchmark for email agents and tool use
3. Why inference cost is falling faster than training cost
4. Five papers worth reading this week

Read the full issue online. You are receiving this because you subscribed to
our newsletter. Unsubscribe at any time.

— The This Week in AI team
```

---

## 预期结果总表

| # | 邮件 | 冲突 | 价值 | 预期结论 | 预期卡片 |
|---|---|---|---|---|---|
| 1 | NVIDIA Challenge（周一 10:00–16:00） | 撞 3 段课 | 5 | **Go anyway** | Due today（注册今天截止） |
| 2 | GDG KL（周六 14:00–17:00） | 无 | 4 | **Go** | Needs a reply |
| 3 | Resume webinar（周四 09:00–10:30） | 撞 WIA2002 | 2 | **Decline** | 不弹卡 |
| 4 | WIA2007 提案（明天 23:59） | — | — | 任务 | **Due tomorrow** |
| 5 | 图书馆延长开放 | — | — | 无行动 | **不弹卡** |
| 6 | RM500 购物券 | — | — | 忽略 | 不弹卡 |
| 7 | AI 周报 #47 | — | — | 忽略 | 不弹卡 |

Digest 页应该显示：**7 个会话、1 个 deadline、2–3 条 needs you、
「can be ignored: 2 newsletters」** 之类的收尾。

**注意**：表格是「预期」，不是保证。价值打分是模型给的，
如果某条判得不对，调邮件里的措辞（加奖项、加具体议程 → 分数上升；
加「有录像」「线上举行」→ 分数下降）。

---

## 发送时序（这一步做错，卡片就不会出现）

`watcher` **第一轮会取静默基线**，把当时收件箱里的所有邮件标记为已见。
所以：

1. **先发 1–6 封**（或全部 7 封），等它们进收件箱
2. 启动 `python scripts/serve.py --watch`，等它跑完第一轮（约 60 秒，静默）
3. **然后再发一封邮件**——就用邮件 4（WIA2007 提醒），它是 urgency 3
4. 等下一轮轮询（≤60 秒），卡片弹出

如果一开始就把 7 封全发完再启动 watcher，**一张卡片都不会有**，因为全在基线里了。

---

## 两个可能翻车的点

**「垃圾邮件」未必被 `is_noise()` 拦掉。**
`is_noise()` 靠 Gmail 自己的 `CATEGORY_PROMOTIONS` 标签和 `no-reply` 发件人来判断，
不是靠模型。你手动发的邮件不会带 promotions 标签，所以它**会进入分析流程**，
只是模型会正确判断为可忽略。

这仍然值得录——「模型判断它不值得打扰你」和「系统按标签过滤」是两回事，
两个都真实。但台词别说成「它被过滤了」，说「它判断这封不值得打扰你」。

**邮件 6 可能直接进垃圾箱。** 如果 Gmail 把 RM500 那封丢进 Spam，
`in:inbox` 查询就看不到它。录之前确认它在**收件箱**里，不在 Spam 文件夹。
