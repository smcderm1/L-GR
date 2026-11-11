import glob
import html
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime

# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
BANGER_MIN_REACTIONS = 4      # Minimum reactions for a "Certified Banger"
PLATINUM_MIN_REACTIONS = 5    # Minimum reactions for a "Platinum Banger"
FLOW_STATE_GAP_MINUTES = 6    # Max time gap between bangers in the same flow state
SILENCE_GAP_HOURS = 2.0       # Threshold to consider conversation dead / restarted
REPORT_OUTPUT_MD = "chat_analysis_report.md"
REPORT_OUTPUT_HTML = "chat_wrapped.html"
REPORT_OUTPUT_HTML_2 = "chat_wrapped_2.html"

STOP_WORDS = set('''
a about above after again against all am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during
each few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's
hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's
me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over
own same shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've this those through to too
under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where where's
which while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours
yourself yourselves just like get got go going know think yeah yes gonna really also one well back see
even now way good say said right much want come look tell take make want come attachment
liked reacted message voted poll attachment audio video chat theme photo
'''.split())

# Regex matching automated Meta notifications
SYSTEM_MSG_RE = re.compile(
    r'(liked a message|reacted .* to your message|sent an attachment|voted .* in the poll|created a poll|changed the theme|named the group)',
    re.IGNORECASE
)

IRISH_SLANG = [
    'craic', 'fella', 'fellas', 'lad', 'lads', 'grand', 'soakage',
    'spoons', 'sound', 'pint', 'pints', 'session', 'gaff', 'chancer',
    'scut', 'bog', 'shift', 'gas', 'class', 'deadly', 'dosser'
]


def fix_encoding(text: str) -> str:
    """Fix Meta's broken latin1-escaped UTF-8 string encoding and normalize aliases."""
    if not isinstance(text, str):
        return text
    try:
        res = text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        res = text

    # Standardize alias: TANGERANG NATIONALIST -> Abyan
    if "TANGERANG NATIONALIST" in res or "TANGERANG" in res:
        res = re.sub(r'TANGERANG( NATIONALIST)?', 'Abyan', res, flags=re.IGNORECASE)
    return res


def load_chat_data(folder_path="."):
    """Find and load all message_*.json files in chronological order."""
    files = sorted(glob.glob(os.path.join(folder_path, "message_*.json")))
    if not files:
        print(f"Error: No 'message_*.json' files found in '{folder_path}'.")
        print("Make sure this script is placed in the exact chat folder.")
        return None

    all_messages = []
    chat_title = "Unknown Chat"

    for file in files:
        print(f"Loading {os.path.basename(file)}...")
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "title" in data:
                chat_title = fix_encoding(data["title"])
            all_messages.extend(data.get("messages", []))

    # Sort strictly chronologically by timestamp_ms
    all_messages.sort(key=lambda m: m.get("timestamp_ms", 0))

    print(f"Total messages loaded: {len(all_messages):,}\n")
    return chat_title, all_messages


def analyze_chat(chat_title, messages):
    if not messages:
        print("No messages found to analyze.")
        return

    # -------------------------------------------------------------------------
    # 1. Timeline & Aggregate Totals
    # -------------------------------------------------------------------------
    start_ts = messages[0].get("timestamp_ms", 0) / 1000.0
    end_ts = messages[-1].get("timestamp_ms", 0) / 1000.0
    start_date = datetime.fromtimestamp(start_ts)
    end_date = datetime.fromtimestamp(end_ts)
    total_days = max(1, (end_date - start_date).days)

    msg_counts = Counter()
    reactions_received = Counter()
    reactions_given = Counter()
    messages_with_reactions = Counter()
    emoji_counts = Counter()
    actor_emoji_types = defaultdict(set)
    actor_non_heart_rx = Counter()
    self_reactions = Counter()

    words_per_user = Counter()
    chars_per_user = Counter()
    msg_with_text_count = Counter()
    longest_msg = {}  # user -> (content, length, date_str)
    caps_count = Counter()
    question_count = Counter()
    laugh_count = Counter()
    slang_counts = defaultdict(Counter)

    media_counts = defaultdict(Counter)

    day_counts = Counter()
    hour_counts = Counter()
    night_owls = Counter()       # 00:00 - 05:00
    early_birds = Counter()      # 06:00 - 10:00
    workday_chatter = Counter()  # 10:00 - 18:00
    evening_chatter = Counter()  # 18:00 - 00:00

    chemistry = defaultdict(Counter)  # [giver][receiver] = count
    all_bangers = []
    unreacted_flops = []

    user_words = defaultdict(Counter)
    total_word_counts = Counter()
    laugh_pattern = re.compile(r'\b(ha(ha)+|lol+|lmao+|lmfao+|dead|crying|ahah+)\b', re.IGNORECASE)

    # -------------------------------------------------------------------------
    # 2. First Pass: Single-Message Metrics
    # -------------------------------------------------------------------------
    for idx, msg in enumerate(messages):
        sender = fix_encoding(msg.get("sender_name", "Unknown"))
        if "tangerang" in sender.lower():
            sender = "Abyan"

        content = fix_encoding(msg.get("content", ""))
        timestamp_ms = msg.get("timestamp_ms", 0)
        reactions = msg.get("reactions", [])
        ts_sec = timestamp_ms / 1000.0 if timestamp_ms else 0
        dt = datetime.fromtimestamp(ts_sec) if ts_sec else None

        msg_counts[sender] += 1

        if dt:
            day_str = dt.strftime("%Y-%m-%d (%A)")
            day_counts[day_str] += 1
            hour_counts[dt.hour] += 1
            if 0 <= dt.hour < 5:
                night_owls[sender] += 1
            elif 6 <= dt.hour < 10:
                early_birds[sender] += 1
            elif 10 <= dt.hour < 18:
                workday_chatter[sender] += 1
            else:
                evening_chatter[sender] += 1

        # Media detection
        if msg.get("photos"):
            media_counts[sender]["photos"] += len(msg["photos"])
        if msg.get("share"):
            media_counts[sender]["shares"] += 1
        if msg.get("audio_files"):
            media_counts[sender]["audio"] += len(msg["audio_files"])
        if msg.get("videos"):
            media_counts[sender]["videos"] += len(msg["videos"])

        # Text analysis (ignoring automated Meta notifications)
        is_system_msg = bool(content and SYSTEM_MSG_RE.search(content))
        if content and not is_system_msg:
            msg_with_text_count[sender] += 1
            words = content.split()
            w_len = len(words)
            c_len = len(content)
            words_per_user[sender] += w_len
            chars_per_user[sender] += c_len

            if sender not in longest_msg or c_len > longest_msg[sender][1]:
                date_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A"
                longest_msg[sender] = (content, c_len, date_str)

            # ALL CAPS detection
            alpha_words = [w for w in re.findall(r'\b[A-Za-z]+\b', content) if len(w) >= 3]
            if alpha_words:
                caps_words = [w for w in alpha_words if w.isupper()]
                if len(caps_words) >= 2 or (len(alpha_words) == 1 and caps_words and len(caps_words[0]) >= 4):
                    caps_count[sender] += 1

            if "?" in content:
                question_count[sender] += 1

            if laugh_pattern.search(content) or "😂" in content or "😭" in content:
                laugh_count[sender] += 1

            # Irish slang detection
            content_lower = content.lower()
            for s_word in IRISH_SLANG:
                if re.search(r'\b' + s_word + r'\b', content_lower):
                    slang_counts[sender][s_word] += 1

            clean_tokens = [w for w in re.findall(r'\b[a-z]{3,}\b', content_lower) if w not in STOP_WORDS]
            for token in clean_tokens:
                user_words[sender][token] += 1
                total_word_counts[token] += 1

            # Track long unreacted flops (crickets)
            if len(reactions) == 0 and c_len >= 120:
                unreacted_flops.append({
                    "sender": sender,
                    "content": content,
                    "chars": c_len,
                    "words": w_len,
                    "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A"
                })

        # Reaction processing
        if reactions:
            reaction_count = len(reactions)
            reactions_received[sender] += reaction_count
            messages_with_reactions[sender] += 1

            reaction_emojis = []
            for r in reactions:
                actor = fix_encoding(r.get("actor", "Unknown"))
                if "tangerang" in actor.lower():
                    actor = "Abyan"

                emoji = fix_encoding(r.get("reaction", ""))

                if actor == sender:
                    self_reactions[sender] += 1

                reactions_given[actor] += 1
                if emoji:
                    emoji_counts[emoji] += 1
                    actor_emoji_types[actor].add(emoji)
                    if emoji not in ["❤", "❤️"]:
                        actor_non_heart_rx[actor] += 1
                    reaction_emojis.append(emoji)

                chemistry[actor][sender] += 1

            if reaction_count >= BANGER_MIN_REACTIONS:
                date_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A"
                clean_text = content.replace("\n", " ").strip() if content else "[Photo / Attachment]"
                all_bangers.append({
                    "idx": idx,
                    "sender": sender,
                    "content": clean_text,
                    "has_text": bool(content and not is_system_msg),
                    "timestamp_ms": timestamp_ms,
                    "rx_count": reaction_count,
                    "emojis": " ".join(reaction_emojis),
                    "date": date_str,
                })

    all_users = sorted(set(msg_counts.keys()).union(set(reactions_given.keys())))

    # -------------------------------------------------------------------------
    # 3. Conversational Pace & Reply Latency
    # -------------------------------------------------------------------------
    reply_counts = Counter()
    latencies_active = defaultdict(list)
    latencies_all = defaultdict(list)
    conversation_starters = Counter()
    conversation_killers = Counter()
    left_on_read = Counter()

    max_streak = defaultdict(int)
    streak_counts = defaultdict(list)
    duo_latencies = defaultdict(list)

    curr_streak_user = None
    curr_streak_len = 0

    for i in range(len(messages)):
        m_curr = messages[i]
        sender_curr = fix_encoding(m_curr.get("sender_name", "Unknown"))
        if "tangerang" in sender_curr.lower():
            sender_curr = "Abyan"
        t_curr = m_curr.get("timestamp_ms", 0)

        # Monologue / streak
        if sender_curr == curr_streak_user:
            curr_streak_len += 1
        else:
            if curr_streak_user:
                streak_counts[curr_streak_user].append(curr_streak_len)
                if curr_streak_len > max_streak[curr_streak_user]:
                    max_streak[curr_streak_user] = curr_streak_len
            curr_streak_user = sender_curr
            curr_streak_len = 1

        # Response to next message
        if i < len(messages) - 1:
            m_next = messages[i + 1]
            sender_next = fix_encoding(m_next.get("sender_name", "Unknown"))
            if "tangerang" in sender_next.lower():
                sender_next = "Abyan"
            t_next = m_next.get("timestamp_ms", 0)
            diff_sec = (t_next - t_curr) / 1000.0

            rx_curr = len(m_curr.get("reactions", []))

            if diff_sec >= SILENCE_GAP_HOURS * 3600:
                conversation_killers[sender_curr] += 1
                conversation_starters[sender_next] += 1
                if rx_curr == 0 and sender_curr != sender_next:
                    left_on_read[sender_curr] += 1

            if sender_curr != sender_next:
                reply_counts[sender_next] += 1
                latencies_all[sender_next].append(diff_sec)
                if diff_sec <= 3600:
                    latencies_active[sender_next].append(diff_sec)
                    duo_latencies[(sender_curr, sender_next)].append(diff_sec)

    if curr_streak_user:
        streak_counts[curr_streak_user].append(curr_streak_len)
        if curr_streak_len > max_streak[curr_streak_user]:
            max_streak[curr_streak_user] = curr_streak_len

    # -------------------------------------------------------------------------
    # 4. Bangers Analysis: Flow States vs One-Liners
    # -------------------------------------------------------------------------
    banger_clusters = []
    current_cluster = []
    gap_ms_limit = FLOW_STATE_GAP_MINUTES * 60 * 1000

    for b in all_bangers:
        if not current_cluster:
            current_cluster.append(b)
        else:
            prev_t = current_cluster[-1]["timestamp_ms"]
            if b["timestamp_ms"] - prev_t <= gap_ms_limit:
                current_cluster.append(b)
            else:
                banger_clusters.append(current_cluster)
                current_cluster = [b]
    if current_cluster:
        banger_clusters.append(current_cluster)

    solo_flow_states = []
    group_flow_states = []
    brilliant_one_liners = []

    solo_flow_user_count = Counter()
    group_flow_user_count = Counter()

    for cluster in banger_clusters:
        if len(cluster) == 1:
            if cluster[0]["has_text"]:
                brilliant_one_liners.append(cluster[0])
        else:
            senders = set(b["sender"] for b in cluster)
            if len(senders) == 1:
                solo_flow_states.append(cluster)
                solo_flow_user_count[list(senders)[0]] += 1
            else:
                group_flow_states.append(cluster)
                for s in senders:
                    group_flow_user_count[s] += 1

    brilliant_one_liners.sort(key=lambda x: x["rx_count"], reverse=True)
    solo_flow_states.sort(key=lambda c: (sum(b["rx_count"] for b in c), len(c)), reverse=True)
    group_flow_states.sort(key=lambda c: (sum(b["rx_count"] for b in c), len(c)), reverse=True)
    unreacted_flops.sort(key=lambda x: x["chars"], reverse=True)

    user_banger_count = Counter()
    user_plat_banger_count = Counter()
    user_one_liner_count = Counter()
    for b in all_bangers:
        user_banger_count[b["sender"]] += 1
        if b["rx_count"] >= PLATINUM_MIN_REACTIONS:
            user_plat_banger_count[b["sender"]] += 1
    for b in brilliant_one_liners:
        user_one_liner_count[b["sender"]] += 1

    # Personal Magnum Opus (Top text message for each person)
    user_magnum_opus = {}
    for m in messages:
        sender = fix_encoding(m.get("sender_name", "Unknown"))
        if "tangerang" in sender.lower():
            sender = "Abyan"
        rx = len(m.get("reactions", []))
        content = fix_encoding(m.get("content", "")).replace("\n", " ").strip()
        is_sys = bool(SYSTEM_MSG_RE.search(content))
        if content and not is_sys:
            if sender not in user_magnum_opus or rx > user_magnum_opus[sender]["rx_count"]:
                dt = datetime.fromtimestamp(m.get("timestamp_ms", 0) / 1000.0) if m.get("timestamp_ms") else None
                user_magnum_opus[sender] = {
                    "rx_count": rx,
                    "content": content,
                    "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A",
                    "emojis": " ".join([fix_encoding(r.get("reaction", "")) for r in m.get("reactions", [])])
                }

    # Distinctive words computation (TF-IDF inspired)
    signature_words = {}
    total_chat_tokens = sum(total_word_counts.values())
    for user, token_counts in user_words.items():
        total_u = sum(token_counts.values())
        if total_u < 40:
            continue
        scores = {}
        for token, count in token_counts.items():
            if count >= 3:
                freq_u = count / total_u
                others_count = total_word_counts[token] - count
                others_total = total_chat_tokens - total_u
                freq_others = (others_count + 1) / (others_total + 1)
                scores[token] = (freq_u / freq_others) * math.log(count + 1)
        top_tokens = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        signature_words[user] = [f"{w} ({token_counts[w]}x)" for w, _ in top_tokens]

    # Chemistry imbalances (unrequited reactions)
    imbalances = []
    for u1 in all_users:
        for u2 in all_users:
            if u1 < u2:
                g1 = chemistry[u1][u2]
                g2 = chemistry[u2][u1]
                diff = g1 - g2
                if abs(diff) >= 25:
                    giver = u1 if diff > 0 else u2
                    receiver = u2 if diff > 0 else u1
                    imbalances.append((giver, receiver, abs(diff), max(g1, g2), min(g1, g2)))
    imbalances.sort(key=lambda x: x[2], reverse=True)

    # Mutual Duos
    mutual_duos = []
    for u1 in all_users:
        for u2 in all_users:
            if u1 < u2:
                tot = chemistry[u1][u2] + chemistry[u2][u1]
                if tot >= 100:
                    mutual_duos.append((u1, u2, tot, chemistry[u1][u2], chemistry[u2][u1]))
    mutual_duos.sort(key=lambda x: x[2], reverse=True)

    # -------------------------------------------------------------------------
    # 5. Build Formatted Output Buffer (for console & markdown export)
    # -------------------------------------------------------------------------
    lines = []

    def p(line=""):
        lines.append(line)
        print(line)

    p("=" * 86)
    p(f"  ULTIMATE GROUP CHAT DOSSIER: {chat_title.upper()}")
    p("=" * 86)
    p(f" Timeline: {start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')} ({total_days:,} days)")
    p(f" Total Messages: {len(messages):,}  |  Avg Daily Volume: {len(messages)/total_days:.1f} msgs/day")
    p(f" Total Reactions Given: {sum(reactions_received.values()):,}  |  Avg Reactions/Msg: {sum(reactions_received.values())/len(messages):.2f}")
    p(f" Media Vault: {sum(media_counts[u]['photos'] for u in media_counts):,} photos, "
      f"{sum(media_counts[u]['shares'] for u in media_counts):,} shared reels/posts, "
      f"{sum(media_counts[u]['videos'] for u in media_counts):,} videos, "
      f"{sum(media_counts[u]['audio'] for u in media_counts):,} voice notes")
    p(f" Record-Breaking Day: {day_counts.most_common(1)[0][0]} ({day_counts.most_common(1)[0][1]:,} messages sent)")
    p(f" Peak Unhinged Hour : {hour_counts.most_common(1)[0][0]:02d}:00 - {hour_counts.most_common(1)[0][0]+1:02d}:00 "
      f"({hour_counts.most_common(1)[0][1]:,} messages total)")

    # SECTION 1
    p("\n" + "=" * 86)
    p("1. PARTICIPANT ACTIVITY & REACTION EFFICIENCY")
    p("=" * 86)
    p(f"{'Participant':<22} | {'Sent':<7} | {'Words':<8} | {'Rx Recv':<8} | {'Hit Rate':<9} | {'Avg Rx':<7} | {'Bangers (4+)':<14}")
    p("-" * 86)
    for user, sent in msg_counts.most_common():
        words = words_per_user[user]
        rx = reactions_received[user]
        hit_rate = (messages_with_reactions[user] / sent * 100) if sent else 0
        avg_rx = (rx / sent) if sent else 0
        b4 = user_banger_count[user]
        b4_pct = (b4 / sent * 100) if sent else 0
        p(f"{user[:21]:<22} | {sent:<7,} | {words:<8,} | {rx:<8,} | {hit_rate:<8.1f}% | {avg_rx:<7.2f} | {b4:<4} ({b4_pct:.1f}%)")

    # SECTION 2
    p("\n" + "=" * 86)
    p("2. COMEDY LAB: BANGERS, ONE-LINERS & FLOW STATES")
    p("=" * 86)
    p(f"Total Certified Bangers (>= {BANGER_MIN_REACTIONS} likes): {len(all_bangers):,}")
    p(f"  * Platinum Bangers (>= {PLATINUM_MIN_REACTIONS} likes): {sum(user_plat_banger_count.values()):,}")
    p(f"  * Standalone One-Liners (Drop-in Zingers): {len(brilliant_one_liners):,}")
    p(f"  * Solo Flow States (Cooking in the Zone): {len(solo_flow_states):,} sessions")
    p(f"  * Group Flow States (Comedy Cyphers): {len(group_flow_states):,} sessions\n")

    p(f"{'Comedian':<22} | {'4+ Bangers':<11} | {'5+ Platinum':<12} | {'One-Liners':<11} | {'Solo Flows':<11} | {'Group Flows':<11}")
    p("-" * 86)
    for user in sorted(all_users, key=lambda u: (user_banger_count[u], user_plat_banger_count[u]), reverse=True):
        if msg_counts[user] == 0:
            continue
        p(f"{user[:21]:<22} | {user_banger_count[user]:<11} | {user_plat_banger_count[user]:<12} | "
          f"{user_one_liner_count[user]:<11} | {solo_flow_user_count[user]:<11} | {group_flow_user_count[user]:<11}")

    p("\n>>> TOP 6 STANDALONE ONE-LINERS (THE SNIPERS) <<<")
    p("High-reaction messages dropped into chat with zero surrounding bangers:\n")
    for idx, b in enumerate(brilliant_one_liners[:6], 1):
        trunc = b["content"][:110] + "..." if len(b["content"]) > 110 else b["content"]
        p(f"#{idx} [{b['rx_count']} reactions] {b['sender']} ({b['date']}):")
        p(f"    \"{trunc}\"")
        p(f"    Reactions: {b['emojis']}\n")

    p(">>> TOP SOLO FLOW STATES (COOKING SOLO) <<<")
    p("When a single person enters an unbroken comedy heater, dropping back-to-back bangers:\n")
    for idx, s in enumerate(solo_flow_states[:3], 1):
        dur = round((s[-1]["timestamp_ms"] - s[0]["timestamp_ms"]) / 60000, 1)
        tot_rx = sum(b["rx_count"] for b in s)
        p(f"#{idx} {s[0]['sender']} on {s[0]['date']} ({len(s)} bangers in {dur} min | {tot_rx} total reactions):")
        for b in s:
            p(f"    [{b['rx_count']} rx] \"{b['content'][:75]}\"")
        p()

    p(">>> TOP GROUP FLOW STATES (THE COMEDY CYPHER) <<<")
    p("When multiple members riff off each other in rapid-fire comedic synchronization:\n")
    for idx, g in enumerate(group_flow_states[:4], 1):
        dur = round((g[-1]["timestamp_ms"] - g[0]["timestamp_ms"]) / 60000, 1)
        tot_rx = sum(b["rx_count"] for b in g)
        p(f"#{idx} Group Riff on {g[0]['date']} ({len(g)} bangers in {dur} min | {tot_rx} total reactions):")
        for b in g:
            p(f"    [{b['rx_count']} rx] {b['sender']}: \"{b['content'][:75]}\"")
        p()

    # SECTION 3: THE CRICKETS
    p("=" * 86)
    p("3. THE CRICKETS AWARD (THE LONGEST UNREACTED FLOPPING ESSAYS)")
    p("=" * 86)
    for idx, f in enumerate(unreacted_flops[:3], 1):
        trunc = f["content"][:110].replace("\n", " ") + "..." if len(f["content"]) > 110 else f["content"]
        p(f"#{idx} {f['sender']} poured their soul out ({f['words']} words, {f['chars']} chars on {f['date']}):")
        p(f"    \"{trunc}\" -> [0 reactions]\n")

    # SECTION 4
    p("=" * 86)
    p("4. CONVERSATIONAL PACE, SPEED DEMONS & LATENCY")
    p("=" * 86)
    p(f"{'Participant':<22} | {'Replies Sent':<13} | {'Active Median':<14} | {'Overall Median':<15} | {'Speed Tier':<12}")
    p("-" * 86)
    for u in sorted(all_users, key=lambda x: reply_counts[x], reverse=True):
        if msg_counts[u] == 0:
            continue
        rc = reply_counts[u]
        med_act = statistics.median(latencies_active[u]) if latencies_active[u] else 0
        med_all = statistics.median(latencies_all[u]) if latencies_all[u] else 0

        if med_act < 18:
            tier = "Lightning ⚡"
        elif med_act < 25:
            tier = "Fast 🏃"
        elif med_act < 40:
            tier = "Steady 🚶"
        else:
            tier = "Sloth 🦥"

        act_str = f"{med_act:.1f}s" if med_act < 60 else f"{med_act/60:.1f}m"
        all_str = f"{med_all:.1f}s" if med_all < 60 else f"{med_all/60:.1f}m"
        p(f"{u[:21]:<22} | {rc:<13,} | {act_str:<14} | {all_str:<15} | {tier:<12}")

    p("\n--- CONVERSATION REVIVERS vs ROOM SILENCERS (Gaps > 2 Hours) ---")
    p(f"{'Participant':<22} | {'Revivers (Breaks Silence)':<26} | {'Silencers (Last Speaker)':<26} | {'Left on Read (0 rx)':<20}")
    p("-" * 98)
    for u in sorted(all_users, key=lambda x: conversation_starters[x], reverse=True):
        if msg_counts[u] == 0:
            continue
        p(f"{u[:21]:<22} | {conversation_starters[u]:<26} | {conversation_killers[u]:<26} | {left_on_read[u]:<20}")

    # SECTION 5
    p("\n" + "=" * 86)
    p("5. CHEMISTRY, SYNERGY & UNREQUITED DEVOTION")
    p("=" * 86)
    p(f"{'Participant':<22} | {'Rx Given':<9} | {'Rx Received':<11} | {'Given/Recv Ratio':<18} | {'Self-Reactions':<14}")
    p("-" * 86)
    for u in sorted(all_users, key=lambda x: reactions_given[x], reverse=True):
        if msg_counts[u] == 0 and reactions_given[u] == 0:
            continue
        given = reactions_given[u]
        recv = reactions_received[u]
        ratio = (given / recv) if recv > 0 else (99.0 if given > 0 else 0)
        self_rx = self_reactions[u]
        p(f"{u[:21]:<22} | {given:<9,} | {recv:<11,} | {ratio:<18.2f} | {self_rx:<14}")

    p("\n--- TOP MUTUAL POWER DUOS (Total Mutual Reactions Shared) ---")
    for u1, u2, tot, g1, g2 in mutual_duos[:6]:
        p(f"  {u1} <---> {u2}: {tot:,} mutual reactions ({u1}: {g1:,} | {u2}: {g2:,})")

    p("\n--- TOP ONE-SIDED DEVOTIONS (The Simp / Unrequited Hype Matrix) ---")
    for giver, receiver, diff, g_gives, r_gives in imbalances[:6]:
        p(f"  {giver} -> {receiver}: Gave {g_gives:,} reactions, but only got {r_gives:,} back (Net +{diff:,})")

    # SECTION 6
    p("\n" + "=" * 86)
    p("6. LINGUISTIC FINGERPRINTS & TEXTING HABITS")
    p("=" * 86)
    p(f"{'Participant':<22} | {'Avg Words':<10} | {'Max Burst':<10} | {'Capslock %':<11} | {'Laughter %':<11} | {'Irish Slang':<12}")
    p("-" * 86)
    for u, sent in msg_counts.most_common():
        txt_cnt = msg_with_text_count[u]
        avg_w = (words_per_user[u] / txt_cnt) if txt_cnt else 0
        burst = max_streak[u]
        caps_pct = (caps_count[u] / txt_cnt * 100) if txt_cnt else 0
        laugh_pct = (laugh_count[u] / txt_cnt * 100) if txt_cnt else 0
        slang_tot = sum(slang_counts[u].values())
        p(f"{u[:21]:<22} | {avg_w:<10.1f} | {burst:<10} | {caps_pct:<10.1f}% | {laugh_pct:<10.1f}% | {slang_tot:<12}")

    p("\n--- SIGNATURE VOCABULARY (Cleaned of automated system strings) ---")
    for u in sorted(signature_words.keys()):
        p(f"  {u[:20]:<20}: {', '.join(signature_words[u])}")

    p("\n--- THE NOVELISTS: LONGEST SINGLE MESSAGES IN CHAT HISTORY ---")
    sorted_novelists = sorted(longest_msg.items(), key=lambda x: x[1][1], reverse=True)
    for u, (content, length, date) in sorted_novelists[:3]:
        snippet = content[:120].replace("\n", " ") + "..." if len(content) > 120 else content
        p(f"  {u} ({length:,} characters on {date}):")
        p(f"    \"{snippet}\"\n")

    # SECTION 7
    p("=" * 86)
    p("7. CIRCADIAN RHYTHMS: WHO SLEEPS AND WHO DOESN'T?")
    p("=" * 86)
    p(f"{'Participant':<22} | {'Night Owl (0-5 AM)':<19} | {'Early Bird (6-10 AM)':<21} | {'Daytime (10-18)':<16} | {'Evening (18-24)':<16}")
    p("-" * 98)
    for u, sent in msg_counts.most_common():
        n_pct = (night_owls[u] / sent * 100) if sent else 0
        e_pct = (early_birds[u] / sent * 100) if sent else 0
        w_pct = (workday_chatter[u] / sent * 100) if sent else 0
        ev_pct = (evening_chatter[u] / sent * 100) if sent else 0
        p(f"{u[:21]:<22} | {night_owls[u]:>4} ({n_pct:>4.1f}%)       | {early_birds[u]:>4} ({e_pct:>4.1f}%)         | {workday_chatter[u]:>5} ({w_pct:>4.1f}%)    | {evening_chatter[u]:>5} ({ev_pct:>4.1f}%)")

    # SECTION 8
    p("\n" + "=" * 86)
    p("8. EMOJI CONNOISSEURS & WILD REACTIONS")
    p("=" * 86)
    p("Most Common Reaction Emojis:")
    for emoji, count in emoji_counts.most_common(10):
        p(f"  {emoji} : {count:,}")

    p("\nEmoji Diversity (Who ventures beyond the standard heart ❤):")
    p(f"{'Participant':<22} | {'Unique Emoji Types':<20} | {'Non-Heart Reactions':<20}")
    p("-" * 66)
    for u in sorted(all_users, key=lambda x: len(actor_emoji_types[x]), reverse=True):
        if reactions_given[u] == 0:
            continue
        p(f"{u[:21]:<22} | {len(actor_emoji_types[u]):<20} | {actor_non_heart_rx[u]:<20,}")

    # SECTION 9
    p("\n" + "=" * 86)
    p("9. PERSONAL MAGNUM OPUS: EVERY MEMBER'S HIGHEST-RATED MESSAGE")
    p("=" * 86)
    sorted_opus = sorted(user_magnum_opus.items(), key=lambda x: x[1]["rx_count"], reverse=True)
    for u, info in sorted_opus:
        snippet = info["content"][:110] + "..." if len(info["content"]) > 110 else info["content"]
        p(f"• {u} [{info['rx_count']} reactions on {info['date']}]:")
        p(f"    \"{snippet}\"")
        p(f"    Reactions: {info['emojis']}\n")

    # SECTION 10
    p("=" * 86)
    p("10. THE OFFICIAL GROUP CHAT SUPERLATIVES & AWARDS CEREMONY")
    p("=" * 86)
    awards = [
        ("🏆 The Standup Headliner", "Seán MacDiarmada",
         f"Highest comedy volume in chat history ({user_banger_count['Seán MacDiarmada']} bangers with 4+ reactions) & All-time record 8 reactions!"),
        ("⚡ Supersonic Speed Demon", "Abyan",
         f"Fastest median response time in the chat (14.4 seconds) — never lets a message hang!"),
        ("🦉 The 3 AM Goblin", "Abyan",
         f"Highest proportion of ungodly late-night texting (7.2% sent between midnight and 5:00 AM)"),
        ("👑 The Activity Sovereign", "Hannah Irvine",
         f"Absolute bedrock of the chat: 3,913 messages, 2,390 replies, 1,691 reactions given, and 206 reels shared!"),
        ("😂 The Laughing Gas Award", "Darren Kelly",
         f"Group chat giggler #1: sent 130 messages laughing (6.1% of all messages sent)"),
        ("🎯 The Ruthless Minimalist", "Sophia",
         f"Absolute master of brevity: hit 7 reactions with just 4 words ('Who wants to go?'), #2 fastest responder (16.1s)"),
        ("🔥 The Flow State Virtuoso", "Joe",
         f"Produced the greatest solo comedy heater in chat history ('i like the solid bar' -> 'crunchy' -> 'chewy' -> 'etc.')"),
        ("📜 The Shakespeare Copypasta King", "Ben Richardson",
         f"Authored the longest single message (944 characters) & held the chat hostage with 14 consecutive messages in a row!"),
        ("⚔️ The Feared Enforcer", "Clodagh🌻",
         f"Mandatory attendance enforcer with an elite 34.1% reaction hit rate and 7-reaction landmark banger!"),
        ("🎩 The Formal Diplomat", "Seán Corry",
         f"Official author of the 'FORMAL NOTICE OF RE-ENTRY' and Hollywood Handshakes capstone with a 36.0% reaction hit rate!"),
        ("🌅 The Early Morning Pogchamp", "ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ",
         f"#1 early bird (10.2% sent 6-10 AM) and single-handedly kept 'poggers' alive with 16 invocations!"),
        ("🙏 The Gracious Host", "Darius Rezania",
         f"Held court with heartfelt post-event thank-you speeches (6 reactions) and 12-message monologues!"),
        ("📈 The Precision Investor", "Aaron Flynn",
         f"Highest reaction hit rate in the chat: 42.9% of his messages receive reactions! Zero wasted words."),
        ("🤝 The Generous Philanthropist", "Pauric Mc Bride",
         f"Gives 1.06 reactions for every message he sends — purely here to support the squad!"),
    ]

    for title, winner, reason in awards:
        p(f"  {title:<35}: {winner}")
        p(f"     -> {reason}\n")
    p("=" * 86)

    # -------------------------------------------------------------------------
    # 6. Save Markdown Report
    # -------------------------------------------------------------------------
    try:
        with open(REPORT_OUTPUT_MD, "w", encoding="utf-8") as f_out:
            f_out.write(f"# Group Chat Dossier: {chat_title}\n\n")
            f_out.write("```\n")
            f_out.write("\n".join(lines))
            f_out.write("\n```\n")
        print(f"\n[+] Full text dossier saved to '{REPORT_OUTPUT_MD}'!")
    except Exception as e:
        print(f"Warning: Could not save markdown file: {e}")

    # -------------------------------------------------------------------------
    # 7. Generate Standalone Keynote Slide Deck HTML Presentation
    # -------------------------------------------------------------------------
    generate_keynote_slide_deck(
        chat_title=chat_title,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        total_msgs=len(messages),
        total_reactions=sum(reactions_received.values()),
        peak_day=day_counts.most_common(1)[0],
        peak_hour=hour_counts.most_common(1)[0],
        all_users=all_users,
        msg_counts=msg_counts,
        reactions_received=reactions_received,
        reactions_given=reactions_given,
        reply_counts=reply_counts,
        latencies_active=latencies_active,
        user_banger_count=user_banger_count,
        messages_with_reactions=messages_with_reactions,
        user_plat_banger_count=user_plat_banger_count,
        brilliant_one_liners=brilliant_one_liners,
        solo_flow_states=solo_flow_states,
        group_flow_states=group_flow_states,
        unreacted_flops=unreacted_flops,
        imbalances=imbalances,
        mutual_duos=mutual_duos,
        signature_words=signature_words,
        user_magnum_opus=user_magnum_opus,
        awards=awards,
        media_counts=media_counts,
        conversation_starters=conversation_starters,
        conversation_killers=conversation_killers,
        left_on_read=left_on_read,
        self_reactions=self_reactions,
        night_owls=night_owls,
        early_birds=early_birds,
        actor_emoji_types=actor_emoji_types,
        emoji_counts=emoji_counts,
        output_path=REPORT_OUTPUT_HTML
    )

    generate_dramatic_keynote_2(
        chat_title=chat_title,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        total_msgs=len(messages),
        total_reactions=sum(reactions_received.values()),
        peak_day=day_counts.most_common(1)[0],
        peak_hour=hour_counts.most_common(1)[0],
        all_users=all_users,
        msg_counts=msg_counts,
        reactions_received=reactions_received,
        reactions_given=reactions_given,
        reply_counts=reply_counts,
        latencies_active=latencies_active,
        user_banger_count=user_banger_count,
        messages_with_reactions=messages_with_reactions,
        user_plat_banger_count=user_plat_banger_count,
        brilliant_one_liners=brilliant_one_liners,
        solo_flow_states=solo_flow_states,
        group_flow_states=group_flow_states,
        unreacted_flops=unreacted_flops,
        imbalances=imbalances,
        mutual_duos=mutual_duos,
        signature_words=signature_words,
        user_magnum_opus=user_magnum_opus,
        awards=awards,
        media_counts=media_counts,
        conversation_starters=conversation_starters,
        conversation_killers=conversation_killers,
        left_on_read=left_on_read,
        self_reactions=self_reactions,
        night_owls=night_owls,
        early_birds=early_birds,
        actor_emoji_types=actor_emoji_types,
        emoji_counts=emoji_counts,
        output_path=REPORT_OUTPUT_HTML_2
    )


def generate_keynote_slide_deck(chat_title, start_date, end_date, total_days,
                                total_msgs, total_reactions, peak_day, peak_hour,
                                all_users, msg_counts, reactions_received, reactions_given,
                                reply_counts, latencies_active, user_banger_count,
                                messages_with_reactions, user_plat_banger_count,
                                brilliant_one_liners, solo_flow_states, group_flow_states,
                                unreacted_flops, imbalances, mutual_duos, signature_words,
                                user_magnum_opus, awards, media_counts,
                                conversation_starters, conversation_killers, left_on_read,
                                self_reactions, night_owls, early_birds, actor_emoji_types,
                                emoji_counts, output_path):
    """Generate a full-screen, keynote presentation slide deck with arrow navigation."""
    esc = html.escape
    slides = []

    # =========================================================================
    # SLIDE 1: TITLE SLIDE
    # =========================================================================
    slides.append(f"""
    <div class="slide-content title-slide">
        <div class="kicker">ANNUAL CHAT RETROSPECTIVE • 2026</div>
        <h1 class="main-title">{esc(chat_title)}</h1>
        <p class="subtitle">The Official Group Chat Keynote &amp; Dossier</p>
        <div class="meta-tag-strip">
            <span class="tag">18,061 Messages</span>
            <span class="tag">7,937 Reactions</span>
            <span class="tag">290 Days of Chaos</span>
            <span class="tag">Zero Filter</span>
        </div>
        <div class="nav-hint">Press <kbd>→</kbd> or <kbd>Space</kbd> to begin presentation</div>
    </div>
    """)

    # =========================================================================
    # SLIDE 2: THE DOSSIER (VITAL SIGNS & HIGH-LEVEL FACTS)
    # =========================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 01</div>
            <h2>The Dossier: Vital Signs</h2>
            <p class="slide-desc">High-level statistics spanning January 19 to November 6, 2026.</p>
        </div>
        <div class="stats-grid">
            <div class="stat-card">
                <span class="number">{total_msgs:,}</span>
                <span class="label">Total Messages</span>
                <span class="detail">{total_msgs/total_days:.1f} messages per day</span>
            </div>
            <div class="stat-card highlight">
                <span class="number">{total_reactions:,}</span>
                <span class="label">Reactions Given</span>
                <span class="detail">0.44 reactions per message average</span>
            </div>
            <div class="stat-card accent">
                <span class="number">{peak_day[1]:,}</span>
                <span class="label">Record-Breaking Day</span>
                <span class="detail">{esc(peak_day[0])}</span>
            </div>
            <div class="stat-card">
                <span class="number">{peak_hour[0]:02d}:00</span>
                <span class="label">Peak Unhinged Hour</span>
                <span class="detail">{peak_hour[1]:,} messages sent at lunch (1-2 PM)</span>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "We sent over 18,000 messages in 10 months. Our absolute peak was Thursday, October 15th with 687 messages in 24 hours."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 3: VOLUME LEADERBOARD (WHO TALKS THE MOST)
    # =========================================================================
    vol_rows = ""
    sorted_vol = msg_counts.most_common()
    top_vol = sorted_vol[0][1]
    for idx, (u, c) in enumerate(sorted_vol, 1):
        pct = (c / total_msgs) * 100
        bar_w = int((c / top_vol) * 100)
        badge_cls = "rank-1" if idx == 1 else ("rank-2" if idx == 2 else ("rank-3" if idx == 3 else "rank-norm"))
        vol_rows += f"""
        <div class="leaderboard-row">
            <span class="rank-badge {badge_cls}">#{idx}</span>
            <span class="user-name"><strong>{esc(u)}</strong></span>
            <div class="progress-wrap">
                <div class="progress-bar" style="width: {bar_w}%;"></div>
            </div>
            <span class="stat-val">{c:,} msgs <small>({pct:.1f}%)</small></span>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 02</div>
            <h2>Volume Leaderboard: The Chatterboxes</h2>
            <p class="slide-desc">Ranking by total raw messages sent in chat history.</p>
        </div>
        <div class="leaderboard-list">
            {vol_rows}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Hannah sent almost 4,000 messages—literally 22% of everything said in this chat came from one person. Darren and Sophia tied for 2nd with over 2,100 each."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 4: THE CLOUT LEADERBOARD (REACTIONS RECEIVED)
    # =========================================================================
    rx_rows = ""
    sorted_rx = reactions_received.most_common()
    top_rx = sorted_rx[0][1]
    for idx, (u, c) in enumerate(sorted_rx, 1):
        bar_w = int((c / top_rx) * 100)
        badge_cls = "rank-1" if idx == 1 else ("rank-2" if idx == 2 else ("rank-3" if idx == 3 else "rank-norm"))
        rx_rows += f"""
        <div class="leaderboard-row">
            <span class="rank-badge {badge_cls}">#{idx}</span>
            <span class="user-name"><strong>{esc(u)}</strong></span>
            <div class="progress-wrap">
                <div class="progress-bar rx-bar" style="width: {bar_w}%;"></div>
            </div>
            <span class="stat-val">{c:,} reactions</span>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 03</div>
            <h2>The Clout Index: Reactions Received</h2>
            <p class="slide-desc">Who got the most love and validation from the squad?</p>
        </div>
        <div class="leaderboard-list">
            {rx_rows}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Hannah takes #1 with 1,560 reactions, but Seán M pulled 1,138 reactions on half the messages sent. Darren finished with 999—literally 1 reaction away from a thousand."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 5: COMEDIC EFFICIENCY & BANGERS (QUALITY OVER QUANTITY)
    # =========================================================================
    eff_cards = ""
    sorted_eff = sorted([u for u in all_users if msg_counts[u] >= 50],
                        key=lambda u: (messages_with_reactions[u] / msg_counts[u]), reverse=True)
    for idx, u in enumerate(sorted_eff[:6], 1):
        s = msg_counts[u]
        rate = (messages_with_reactions[u] / s) * 100
        b4 = user_banger_count[u]
        eff_cards += f"""
        <div class="card metric-box">
            <div class="metric-top">
                <span class="rank-pill">#{idx}</span>
                <span class="metric-val">{rate:.1f}%</span>
            </div>
            <div class="metric-user">{esc(u)}</div>
            <div class="metric-sub">{b4} Certified Bangers (4+ rx) • {s:,} sent</div>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 04</div>
            <h2>Comedic Efficiency: Hit Rate &amp; Bangers</h2>
            <p class="slide-desc">Percentage of messages that earned at least 1 reaction (Quality over Quantity).</p>
        </div>
        <div class="grid-3">
            {eff_cards}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Aaron Flynn doesn't speak often, but when he does, 43% of his messages get reacted to. Pure sniper efficiency. Seán Corry and Abyan follow close behind with 36%."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 6: SPEED DEMONS: FASTEST DRAW IN THE WEST
    # =========================================================================
    speed_items = ""
    sorted_speed = sorted(
        [u for u in all_users if msg_counts[u] >= 50],
        key=lambda u: statistics.median(latencies_active[u]) if latencies_active[u] else 999
    )
    for idx, u in enumerate(sorted_speed, 1):
        med = statistics.median(latencies_active[u]) if latencies_active[u] else 0
        med_str = f"{med:.1f}s" if med < 60 else f"{med/60:.1f}m"
        tier = "⚡ Supersonic" if med < 17 else ("🏃 Rapid" if med < 22 else ("🚶 Steady" if med < 35 else "🦥 Patient"))
        speed_items += f"""
        <div class="speed-row">
            <span class="speed-rank">#{idx}</span>
            <span class="speed-user">{esc(u)}</span>
            <span class="speed-time">{med_str}</span>
            <span class="speed-tier">{tier}</span>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 05</div>
            <h2>Speed Demons: Fastest Draw in the West</h2>
            <p class="slide-desc">Median active response latency when another person finishes speaking.</p>
        </div>
        <div class="speed-grid">
            {speed_items}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Abyan has lightning fingers with a 14.4 second median reply time. Sophia is right on his heels at 16.1s. Meanwhile, Aaron Flynn takes 80 seconds—he ponders his thoughts."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 7: CONVERSATION STARTERS VS ROOM SILENCERS
    # =========================================================================
    starters_top = conversation_starters.most_common(4)
    killers_top = conversation_killers.most_common(4)
    ghost_top = left_on_read.most_common(4)

    def render_col(title, emoji, items, note):
        rows = "".join([f'<li><strong>{esc(u)}</strong>: {c} times</li>' for u, c in items])
        return f"""
        <div class="card column-card">
            <h3>{emoji} {title}</h3>
            <p class="col-note">{note}</p>
            <ul>{rows}</ul>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 06</div>
            <h2>The Defibrillators vs The Room Silencers</h2>
            <p class="slide-desc">Tracking conversational dynamics around 2+ hour graveyard silences.</p>
        </div>
        <div class="grid-3">
            {render_col("The Defibrillators", "🚒", starters_top, "Broke an awkward 2+ hour silence")}
            {render_col("The Room Silencers", "💀", killers_top, "Sent the message before the chat died")}
            {render_col("Left in The Void", "🕳️", ghost_top, "0 reactions followed by >2h silence")}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Hannah broke the silence 105 times, but also caused the silence 109 times. Abyan is #2 in both categories—they are the dual engines of life and death in this chat."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 8: THE MEDIA VAULT & REEL JUNKIES
    # =========================================================================
    top_photos = sorted(all_users, key=lambda u: media_counts[u]["photos"], reverse=True)[:4]
    top_shares = sorted(all_users, key=lambda u: media_counts[u]["shares"], reverse=True)[:4]

    photo_list = "".join([f'<li><strong>{esc(u)}</strong>: {media_counts[u]["photos"]} photos</li>' for u in top_photos])
    shares_list = "".join([f'<li><strong>{esc(u)}</strong>: {media_counts[u]["shares"]} shared reels</li>' for u in top_shares])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 07</div>
            <h2>The Media Vault &amp; Reel Junkies</h2>
            <p class="slide-desc">Who sends the photos, who bombards the group with Instagram Reels?</p>
        </div>
        <div class="grid-2">
            <div class="card media-box">
                <h3>📸 The Paparazzi (Photos Sent)</h3>
                <p class="subtext">529 total photos dropped in chat history</p>
                <ul>{photo_list}</ul>
            </div>
            <div class="card media-box highlight-box">
                <h3>📲 The Reel Addicts (Shared Posts)</h3>
                <p class="subtext">504 shared Instagram posts/reels forwarded</p>
                <ul>{shares_list}</ul>
            </div>
        </div>
        <div class="callout-card">
            🎙️ <strong>The Voice Note Fact:</strong> Exactly <strong>TWO voice notes</strong> were sent in 10 months across 18,061 messages. One by Hannah, one by Ben. Nobody wants to hear our voices.
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Hannah sent 206 reels—literally 41% of every reel in this chat came from Hannah. Hannah and Ben also share the solitary voice note record."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 9: THE CRICKETS AWARD (THE LONGEST UNREACTED ESSAYS)
    # =========================================================================
    crickets_cards = ""
    for idx, f in enumerate(unreacted_flops[:3], 1):
        trunc = f["content"][:220].replace("\n", " ") + "..." if len(f["content"]) > 220 else f["content"]
        crickets_cards += f"""
        <div class="card cricket-card">
            <div class="cricket-header">
                <strong>#{idx} {esc(f['sender'])}</strong>
                <span class="badge rx-zero">0 Reactions • {f['words']} Words ({f['chars']} chars)</span>
            </div>
            <p class="cricket-quote">“{esc(trunc)}”</p>
            <div class="cricket-date">{esc(f['date'])} • Result: Total Dead Silence 🦗</div>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 08</div>
            <h2>The Crickets Award: Poured Their Soul Out</h2>
            <p class="slide-desc">The longest essays and manifestos that received literally ZERO reactions.</p>
        </div>
        <div class="cricket-container">
            {crickets_cards}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Ben wrote a 944-character essay about brunettes in lecture halls and got absolute crickets. Daniel dropped an interior design thesis on dark grey wooden floors—zero reactions."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 10: CROWNING ONE-LINERS (THE SNIPERS)
    # =========================================================================
    one_liner_cards = ""
    for idx, b in enumerate(brilliant_one_liners[:4], 1):
        trunc = b["content"][:130] + "..." if len(b["content"]) > 130 else b["content"]
        one_liner_cards += f"""
        <div class="card sniper-card">
            <div class="sniper-top">
                <span class="badge rx-badge">#{idx} ★ {b['rx_count']} Reactions</span>
                <span class="sniper-date">{esc(b['date'])}</span>
            </div>
            <p class="sniper-text">“{esc(trunc)}”</p>
            <div class="sniper-footer">
                <strong>{esc(b['sender'])}</strong>
                <span>{esc(b['emojis'])}</span>
            </div>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 09</div>
            <h2>Crowning One-Liners: The Comedy Snipers</h2>
            <p class="slide-desc">Standalone bangers dropped out of nowhere with zero surrounding liked messages.</p>
        </div>
        <div class="grid-2">
            {one_liner_cards}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Seán M's 21st Swords party invite holds the #1 all-time record with 8 reactions. Sophia's 4-word message 'Who wants to go?' got 7 reactions—pure poetry."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 11: SOLO FLOW STATE (COOKING IN THE ZONE)
    # =========================================================================
    solo_joe = next((c for c in solo_flow_states if c and c[0]["sender"] == "Joe" and len(c) >= 3),
                    (solo_flow_states[0] if solo_flow_states else []))

    joe_bubbles = "".join([f'<div class="chat-bubble joe-bubble"><span class="rx-pill">{b["rx_count"]} rx</span> “{esc(b["content"])}”</div>' for b in solo_joe])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 10</div>
            <h2>Solo Flow State: Cooking in the Zone</h2>
            <p class="slide-desc">When one person enters an unbroken manic comedy heater completely alone.</p>
        </div>
        <div class="card flow-showcase">
            <div class="flow-meta">
                <h3>👑 Joe's 30-Second Symphony</h3>
                <span>July 15, 2026 • 20 Total Reactions in half a minute</span>
            </div>
            <div class="bubble-stack">
                {joe_bubbles}
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Within 30 seconds, Joe fired off four messages in a row: 'i like the solid bar', 'crunchy', 'chewy', 'etc.' Every single one became a certified banger."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 12: GROUP FLOW STATE #1 (GOONER PARLIAMENTARY DEBATE)
    # =========================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 1</div>
            <h2>The Parliamentary Gooner Debate</h2>
            <p class="slide-desc">October 20, 2026 • 4 Bangers in 6 Minutes (20 Total Reactions)</p>
        </div>
        <div class="chat-dialogue">
            <div class="chat-row left">
                <span class="avatar">Joe</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “what's unethical about sucking your own dick”</div>
            </div>
            <div class="chat-row right">
                <span class="avatar">ᴅᴀɴɪᴇʟ</span>
                <div class="bubble"><span class="rx-tag">4 rx</span> “Whats unethical about trying to suck it yourself?”</div>
            </div>
            <div class="chat-row left">
                <span class="avatar">Seán C</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> ““Mr speaker I am a gooner” ahh”</div>
            </div>
            <div class="chat-row right">
                <span class="avatar">Hannah</span>
                <div class="bubble"><span class="rx-tag">6 rx</span> “Of he can do this for himself imagine what h could do for me”</div>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "An absolute masterclass in conversational improv. Four people, four distinct angles, unbroken comedic synchronization."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 13: GROUP FLOW STATE #2 (SLANDER & TEMU DARREN)
    # =========================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 2</div>
            <h2>Temu Darren &amp; Two-Line Slander</h2>
            <p class="slide-desc">Rapid-fire comedic execution across July &amp; October 2026.</p>
        </div>
        <div class="grid-2">
            <div class="card riff-card">
                <h3>Riff A: The Temu Incident</h3>
                <div class="dialogue-mini">
                    <div class="msg"><strong>Darren Kelly</strong> [4 rx]: “The goat???”</div>
                    <div class="msg reply"><strong>Sophia</strong> [4 rx]: “David Kenny aka temu Darren Kelly”</div>
                </div>
            </div>
            <div class="card riff-card">
                <h3>Riff B: Two-Line Acronym Slander</h3>
                <div class="dialogue-mini">
                    <div class="msg"><strong>Darren Kelly</strong> [5 rx]: “The s in smacks stands for si”</div>
                    <div class="msg reply"><strong>Seán MacDiarmada</strong> [6 rx]: “the o in joe stands for obese”</div>
                </div>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Sophia calling David Kenny 'Temu Darren Kelly' was unprovoked and devastating. And Seán's rebuttal on Joe's acronym was pure violence."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 14: GROUP FLOW STATE #3 (FATHERHOOD & THE XENOMORPH)
    # =========================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 3</div>
            <h2>Fatherhood &amp; The Xenomorph</h2>
            <p class="slide-desc">August 27, 2026 • From pure emotional warmth to immediate chaos in 60 seconds.</p>
        </div>
        <div class="chat-dialogue">
            <div class="chat-row left">
                <span class="avatar">Ben</span>
                <div class="bubble wide"><span class="rx-tag">6 rx</span> “So beautiful and amazing and I would love to be a father and be blessed to have a lovely wife and child... cuddle in bed in the morning as a family...”</div>
            </div>
            <div class="chat-row left">
                <span class="avatar">Ben</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “Pffffff Alri ig”</div>
            </div>
            <div class="chat-row right">
                <span class="avatar">Seán M</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “"are you a xenomorph because I'd let you burst on my chest" - Darren”</div>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Ben wrote the most wholesome paragraph in chat history about loving a child, followed immediately by Seán quoting Darren's pickup lines about Xenomorphs."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 15: CHEMISTRY & THE SIMP MATRIX
    # =========================================================================
    simp_rows = ""
    for giver, receiver, diff, g_gives, r_gives in imbalances[:4]:
        simp_rows += f"""
        <div class="simp-card">
            <div class="simp-pair"><strong>{esc(giver)}</strong> ➔ <strong>{esc(receiver)}</strong></div>
            <div class="simp-detail">Gave {g_gives:,} rx • Received only {r_gives:,} rx back</div>
            <div class="simp-diff">Net Deficit: -{diff:,} Reactions 💔</div>
        </div>
        """

    duo_rows = "".join([f'<li><strong>{esc(u1)} &amp; {esc(u2)}</strong>: {tot:,} mutual reactions</li>' for u1, u2, tot, _, _ in mutual_duos[:3]])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 11</div>
            <h2>Chemistry &amp; The Simp Matrix</h2>
            <p class="slide-desc">Mutual power duos versus the biggest one-sided reaction deficits.</p>
        </div>
        <div class="grid-2">
            <div class="card">
                <h3>💞 Top Mutual Duos</h3>
                <p class="subtext">Equal exchange of love and hype</p>
                <ul class="styled-list">{duo_rows}</ul>
            </div>
            <div class="card">
                <h3>💔 The Simp Index (Unrequited Hype)</h3>
                <p class="subtext">Highest reaction imbalances in the chat</p>
                {simp_rows}
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Daniel gave Hannah 268 reactions and only got 95 back. Hannah gave Seán M 274 reactions and got 121 back. Joe gave Seán M 190 reactions and only got 45 back. The food chain is brutal."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 16: THE NARCISSUS TROPHY (SELF-REACTIONS)
    # =========================================================================
    self_items = "".join([f'<div class="podium-item"><span class="badge self-badge">{c}x</span> <strong>{esc(u)}</strong></div>'
                          for u, c in self_reactions.most_common(5)])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 12</div>
            <h2>The Narcissus Trophy: Self-Reactions</h2>
            <p class="slide-desc">Caught in 4K reacting with emojis to their own sent messages.</p>
        </div>
        <div class="card narcissus-box">
            <div class="trophy-icon">🪞</div>
            <div class="podium-row">
                {self_items}
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Seán M and Daniel Cairney are tied at the top with 9 self-reactions each. Abyan was caught 6 times. Self-love is important, guys."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 17: SLEEP SCHEDULES: 3 AM GOBLINS VS EARLY BIRDS
    # =========================================================================
    owl_rows = "".join([f'<li><strong>{esc(u)}</strong>: {night_owls[u]} msgs ({night_owls[u]/msg_counts[u]*100:.1f}%)</li>'
                        for u in sorted(all_users, key=lambda x: night_owls[x]/msg_counts[x] if msg_counts[x] > 50 else 0, reverse=True)[:4]])
    bird_rows = "".join([f'<li><strong>{esc(u)}</strong>: {early_birds[u]} msgs ({early_birds[u]/msg_counts[u]*100:.1f}%)</li>'
                         for u in sorted(all_users, key=lambda x: early_birds[x]/msg_counts[x] if msg_counts[x] > 50 else 0, reverse=True)[:4]])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 13</div>
            <h2>Circadian Rhythms: Sleep Schedules</h2>
            <p class="slide-desc">Who texts at ungodly hours, and who is awake before the sun?</p>
        </div>
        <div class="grid-2">
            <div class="card time-card owl-bg">
                <h3>🦉 The 3 AM Goblins (Midnight – 5 AM)</h3>
                <ul class="styled-list">{owl_rows}</ul>
            </div>
            <div class="card time-card bird-bg">
                <h3>🌅 The Early Birds (6 AM – 10 AM)</h3>
                <ul class="styled-list">{bird_rows}</ul>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Abyan leads the vampire brigade with over 7% of all his messages sent between midnight and 5 AM. Daniel Cairney is our early morning pogchamp at 10.2% before 10 AM. Clodagh is comatose at 1.1%."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 18: SIGNATURE VOCABULARY
    # =========================================================================
    vocab_cards = ""
    for u in sorted(signature_words.keys())[:8]:
        words_joined = " • ".join(signature_words[u])
        vocab_cards += f"""
        <div class="card vocab-box">
            <strong>{esc(u)}</strong>
            <p>{esc(words_joined)}</p>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 14</div>
            <h2>Signature Vocabulary: Linguistic Fingerprints</h2>
            <p class="slide-desc">Cleaned distinctive words used disproportionately by each person.</p>
        </div>
        <div class="grid-2">
            {vocab_cards}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Daniel said poggers 16 times and invented 'dargin' and 'yargin'. Joe talks about toasties and pulling. Abyan dropped 'hypergamy'. Darren's life revolves around climbing and IBEC."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 19: EMOJI CONNOISSEURS & WILD REACTIONS
    # =========================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 15</div>
            <h2>Emoji Connoisseurs &amp; Wild Reactions</h2>
            <p class="slide-desc">Over 155 distinct emojis were used as reactions in this chat.</p>
        </div>
        <div class="grid-2">
            <div class="card">
                <h3>👑 The Standard Emoji Hierarchy</h3>
                <div class="emoji-stat">❤ Heart: <strong>6,627x</strong></div>
                <div class="emoji-stat">🔥 Fire: <strong>112x</strong></div>
                <div class="emoji-stat">😂 Laugh: <strong>93x</strong></div>
                <div class="emoji-stat">😢 Cry: <strong>86x</strong></div>
            </div>
            <div class="card highlight-box">
                <h3>🧌 The Unhinged Emoji Collection</h3>
                <div class="emoji-stat">🐐 Goat: <strong>61x</strong></div>
                <div class="emoji-stat">🇮🇱 Flag: <strong>78x</strong></div>
                <div class="emoji-stat">🗜️ Metal Clamp: <strong>6x</strong></div>
                <div class="emoji-stat">🧼 Bar of Soap: <strong>4x</strong></div>
                <div class="emoji-stat">⚰️ Coffin: <strong>2x</strong></div>
                <div class="emoji-stat">🔪 Kitchen Knife: <strong>2x</strong></div>
            </div>
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Hannah used 55 different reaction emojis, Seán M used 51. Someone in this group reacted with a metal clamp 6 times and soap 4 times."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 20: MAGNUM OPUS (PART 1)
    # =========================================================================
    sorted_opus = sorted(user_magnum_opus.items(), key=lambda x: x[1]["rx_count"], reverse=True)
    opus_cards_1 = ""
    for u, info in sorted_opus[:6]:
        snippet = info["content"][:110] + "..." if len(info["content"]) > 110 else info["content"]
        opus_cards_1 += f"""
        <div class="card opus-slide-card">
            <div class="opus-top">
                <strong>{esc(u)}</strong>
                <span class="badge rx-badge">{info['rx_count']} Reactions</span>
            </div>
            <p class="opus-quote">“{esc(snippet)}”</p>
            <div class="opus-meta">{esc(info['date'])} • {esc(info['emojis'])}</div>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 16 • PART 1</div>
            <h2>Magnum Opus: Crowning Achievements</h2>
            <p class="slide-desc">Every member's single highest-rated message in group chat history.</p>
        </div>
        <div class="grid-2">
            {opus_cards_1}
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 21: MAGNUM OPUS (PART 2)
    # =========================================================================
    opus_cards_2 = ""
    for u, info in sorted_opus[6:]:
        snippet = info["content"][:110] + "..." if len(info["content"]) > 110 else info["content"]
        opus_cards_2 += f"""
        <div class="card opus-slide-card">
            <div class="opus-top">
                <strong>{esc(u)}</strong>
                <span class="badge rx-badge">{info['rx_count']} Reactions</span>
            </div>
            <p class="opus-quote">“{esc(snippet)}”</p>
            <div class="opus-meta">{esc(info['date'])} • {esc(info['emojis'])}</div>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 16 • PART 2</div>
            <h2>Magnum Opus: Crowning Achievements</h2>
            <p class="slide-desc">Every member's single highest-rated message in group chat history.</p>
        </div>
        <div class="grid-2">
            {opus_cards_2}
        </div>
        <div class="speaker-card">
            🗣️ <strong>Speaker Note:</strong> "Abyan's crowning achievement was getting an IMF Malaysian fridge magnet. Joe's was challenging Rootbeer to 1v1 crossclimb. Everyone had their moment."
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 22: THE ANNUAL SUPERLATIVES & AWARDS (PART 1)
    # =========================================================================
    awards_cards_1 = ""
    for title, winner, reason in awards[:7]:
        awards_cards_1 += f"""
        <div class="card award-slide-card">
            <div class="award-trophy">{esc(title)}</div>
            <div class="award-recipient">{esc(winner)}</div>
            <p class="award-desc">{esc(reason)}</p>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 17 • PART 1</div>
            <h2>The Official Superlatives Ceremony</h2>
            <p class="slide-desc">Data-backed accolades awarded to each participant.</p>
        </div>
        <div class="grid-3">
            {awards_cards_1}
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 23: THE ANNUAL SUPERLATIVES & AWARDS (PART 2)
    # =========================================================================
    awards_cards_2 = ""
    for title, winner, reason in awards[7:]:
        awards_cards_2 += f"""
        <div class="card award-slide-card">
            <div class="award-trophy">{esc(title)}</div>
            <div class="award-recipient">{esc(winner)}</div>
            <p class="award-desc">{esc(reason)}</p>
        </div>
        """

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 17 • PART 2</div>
            <h2>The Official Superlatives Ceremony</h2>
            <p class="slide-desc">Data-backed accolades awarded to each participant.</p>
        </div>
        <div class="grid-3">
            {awards_cards_2}
        </div>
    </div>
    """)

    # =========================================================================
    # SLIDE 24: FINALE SLIDE
    # =========================================================================
    slides.append(f"""
    <div class="slide-content finale-slide">
        <div class="kicker">THAT'S A WRAP • 2026</div>
        <h1 class="main-title">Thank You, Lemmings &amp; Gooners.</h1>
        <p class="subtitle">Here's to another 18,000 unhinged messages.</p>
        <div class="meta-tag-strip">
            <span class="tag">Dossier Complete</span>
            <span class="tag">Safe &amp; Sound</span>
            <span class="tag">#eattheSU</span>
        </div>
        <button class="restart-btn" onclick="goToSlide(0)">↺ Replay Presentation</button>
    </div>
    """)

    # Wrap slides into container
    slides_html = ""
    for idx, s in enumerate(slides):
        active_cls = " active" if idx == 0 else ""
        slides_html += f'<section class="slide{active_cls}" data-slide="{idx}">{s}</section>\n'

    # Master HTML template
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(chat_title)} - 2026 Keynote Presentation</title>
<style>
    :root {{
        --bg-main: #0c0d14;
        --bg-card: rgba(26, 29, 46, 0.75);
        --bg-card-hover: rgba(35, 39, 60, 0.9);
        --text-bright: #ffffff;
        --text-sub: #a1adcb;
        --accent-purple: #8b5cf6;
        --accent-pink: #ec4899;
        --accent-cyan: #06b6d4;
        --accent-gold: #f59e0b;
        --accent-green: #10b981;
        --border-glass: rgba(255, 255, 255, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        background: var(--bg-main);
        color: var(--text-bright);
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        overflow: hidden;
        height: 100vh;
        width: 100vw;
        user-select: none;
    }}

    /* Presentation Deck */
    .deck {{
        position: relative;
        width: 100vw;
        height: 100vh;
        overflow: hidden;
    }}
    .slide {{
        position: absolute;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        display: flex;
        justify-content: center;
        align-items: center;
        opacity: 0;
        pointer-events: none;
        transform: scale(0.98) translateY(15px);
        transition: opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1), transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
        padding: 3rem 4rem 5rem;
    }}
    .slide.active {{
        opacity: 1;
        pointer-events: auto;
        transform: scale(1) translateY(0);
    }}

    .slide-content {{
        max-width: 1150px;
        width: 100%;
        max-height: 85vh;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }}

    /* Headers */
    .kicker {{
        font-size: 0.85rem;
        font-weight: 800;
        letter-spacing: 2px;
        color: var(--accent-cyan);
        text-transform: uppercase;
        margin-bottom: 0.3rem;
    }}
    h1.main-title {{
        font-size: 4rem;
        font-weight: 900;
        line-height: 1.1;
        background: linear-gradient(135deg, #c084fc 0%, #f472b6 50%, #38bdf8 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }}
    h2 {{
        font-size: 2.3rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }}
    .slide-desc {{
        color: var(--text-sub);
        font-size: 1.05rem;
        margin-bottom: 1.5rem;
    }}
    .subtitle {{
        font-size: 1.5rem;
        color: var(--text-sub);
        margin-bottom: 2rem;
    }}

    /* Card styling */
    .card {{
        background: var(--bg-card);
        border: 1px solid var(--border-glass);
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        backdrop-filter: blur(16px);
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
    }}
    .highlight {{ border-color: rgba(6, 182, 212, 0.35); }}
    .accent {{ border-color: rgba(245, 158, 11, 0.35); }}

    /* Grids */
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1.2rem; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.2rem; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.2rem; margin-bottom: 1.5rem; }}

    .stat-card {{
        background: var(--bg-card);
        border: 1px solid var(--border-glass);
        border-radius: 16px;
        padding: 1.8rem 1.4rem;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }}
    .stat-card .number {{
        font-size: 2.8rem;
        font-weight: 900;
        color: var(--accent-cyan);
        line-height: 1;
        margin-bottom: 0.5rem;
    }}
    .stat-card .label {{
        font-size: 0.9rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #fff;
    }}
    .stat-card .detail {{
        font-size: 0.85rem;
        color: var(--text-sub);
        margin-top: 0.4rem;
    }}

    /* Leaderboards */
    .leaderboard-list {{
        display: flex;
        flex-direction: column;
        gap: 0.45rem;
        max-height: 52vh;
        overflow-y: auto;
        padding-right: 0.5rem;
    }}
    .leaderboard-row {{
        display: flex;
        align-items: center;
        gap: 1rem;
        background: var(--bg-card);
        border: 1px solid var(--border-glass);
        padding: 0.55rem 1rem;
        border-radius: 10px;
    }}
    .rank-badge {{
        font-size: 0.85rem;
        font-weight: 800;
        padding: 0.2rem 0.5rem;
        border-radius: 6px;
        min-width: 38px;
        text-align: center;
    }}
    .rank-1 {{ background: rgba(245, 158, 11, 0.25); color: #f59e0b; }}
    .rank-2 {{ background: rgba(148, 163, 184, 0.25); color: #cbd5e1; }}
    .rank-3 {{ background: rgba(180, 83, 9, 0.25); color: #d97706; }}
    .rank-norm {{ background: rgba(255, 255, 255, 0.08); color: var(--text-sub); }}
    .user-name {{ width: 180px; font-size: 1rem; }}
    .progress-wrap {{ flex: 1; height: 10px; background: rgba(255, 255, 255, 0.06); border-radius: 6px; overflow: hidden; }}
    .progress-bar {{ height: 100%; background: linear-gradient(90deg, #8b5cf6, #ec4899); border-radius: 6px; }}
    .rx-bar {{ background: linear-gradient(90deg, #ec4899, #f59e0b); }}
    .stat-val {{ width: 140px; text-align: right; font-weight: 700; font-size: 0.95rem; }}

    /* Speed Demons */
    .speed-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.7rem; max-height: 55vh; overflow-y: auto; }}
    .speed-row {{
        background: var(--bg-card);
        border: 1px solid var(--border-glass);
        padding: 0.7rem 1.2rem;
        border-radius: 12px;
        display: flex;
        align-items: center;
        gap: 0.8rem;
    }}
    .speed-rank {{ font-weight: 800; color: var(--accent-gold); width: 28px; }}
    .speed-user {{ flex: 1; font-weight: 700; }}
    .speed-time {{ font-size: 1.15rem; font-weight: 800; color: var(--accent-cyan); }}
    .speed-tier {{ font-size: 0.85rem; color: var(--text-sub); padding: 0.15rem 0.5rem; background: rgba(255, 255, 255, 0.06); border-radius: 6px; }}

    /* Speaker Notes Box */
    .speaker-card {{
        margin-top: 1.3rem;
        background: rgba(139, 92, 246, 0.12);
        border: 1px dashed rgba(139, 92, 246, 0.4);
        padding: 0.8rem 1.2rem;
        border-radius: 10px;
        font-size: 0.95rem;
        color: #d8b4fe;
    }}

    /* Quotes & Dialogues */
    .chat-dialogue {{
        display: flex;
        flex-direction: column;
        gap: 0.9rem;
        margin: 1rem 0;
    }}
    .chat-row {{ display: flex; align-items: center; gap: 1rem; }}
    .chat-row.right {{ flex-direction: row-reverse; }}
    .avatar {{
        font-size: 0.85rem;
        font-weight: 800;
        background: rgba(255, 255, 255, 0.1);
        padding: 0.35rem 0.75rem;
        border-radius: 20px;
        min-width: 80px;
        text-align: center;
    }}
    .bubble {{
        background: var(--bg-card);
        border: 1px solid var(--border-glass);
        padding: 0.9rem 1.3rem;
        border-radius: 18px;
        font-size: 1.1rem;
        position: relative;
        max-width: 75%;
    }}
    .bubble.wide {{ font-size: 0.98rem; }}
    .chat-row.right .bubble {{ background: rgba(139, 92, 246, 0.25); border-color: rgba(139, 92, 246, 0.4); }}
    .rx-tag {{
        display: inline-block;
        background: rgba(236, 72, 153, 0.25);
        color: #f472b6;
        font-weight: 800;
        font-size: 0.75rem;
        padding: 0.15rem 0.45rem;
        border-radius: 6px;
        margin-right: 0.4rem;
    }}

    /* Crickets */
    .cricket-container {{ display: flex; flex-direction: column; gap: 1rem; }}
    .cricket-card {{ border-left: 4px solid var(--accent-pink); }}
    .cricket-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }}
    .cricket-quote {{ font-size: 1.05rem; font-style: italic; color: #f1f5f9; margin-bottom: 0.4rem; }}
    .cricket-date {{ font-size: 0.8rem; color: var(--text-sub); }}
    .rx-zero {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}

    /* Awards */
    .award-slide-card {{
        border: 1px solid rgba(245, 158, 11, 0.3);
        background: linear-gradient(180deg, rgba(245, 158, 11, 0.08) 0%, rgba(26, 29, 46, 0.8) 100%);
    }}
    .award-trophy {{ font-size: 0.85rem; font-weight: 800; color: var(--accent-gold); text-transform: uppercase; letter-spacing: 1px; }}
    .award-recipient {{ font-size: 1.35rem; font-weight: 800; margin: 0.3rem 0; color: #fff; }}
    .award-desc {{ font-size: 0.88rem; color: var(--text-sub); }}

    /* Tags & Navigation */
    .meta-tag-strip {{ display: flex; gap: 0.8rem; margin-bottom: 2.5rem; }}
    .tag {{ background: rgba(255, 255, 255, 0.08); padding: 0.4rem 0.9rem; border-radius: 20px; font-size: 0.95rem; font-weight: 600; }}
    .nav-hint {{ font-size: 0.95rem; color: var(--text-sub); }}
    kbd {{ background: rgba(255, 255, 255, 0.15); padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 700; color: #fff; }}

    /* Controls overlay */
    .deck-controls {{
        position: fixed;
        bottom: 1.5rem;
        left: 50%;
        transform: translateX(-50%);
        display: flex;
        align-items: center;
        gap: 1rem;
        background: rgba(15, 17, 26, 0.8);
        border: 1px solid var(--border-glass);
        padding: 0.5rem 1rem;
        border-radius: 30px;
        backdrop-filter: blur(12px);
        z-index: 100;
    }}
    .ctrl-btn {{
        background: none;
        border: none;
        color: #fff;
        font-size: 1.2rem;
        cursor: pointer;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        transition: background 0.2s;
    }}
    .ctrl-btn:hover {{ background: rgba(255, 255, 255, 0.1); }}
    .slide-counter {{ font-size: 0.85rem; font-weight: 700; color: var(--text-sub); min-width: 80px; text-align: center; }}
    .progress-line {{
        position: fixed;
        top: 0;
        left: 0;
        height: 3px;
        background: linear-gradient(90deg, #8b5cf6, #ec4899, #06b6d4);
        transition: width 0.3s ease;
        z-index: 101;
    }}
    .restart-btn {{
        background: linear-gradient(135deg, #8b5cf6, #ec4899);
        border: none;
        color: #fff;
        padding: 0.8rem 1.8rem;
        font-size: 1.1rem;
        font-weight: 700;
        border-radius: 30px;
        cursor: pointer;
        margin-top: 1.5rem;
    }}
</style>
</head>
<body>

<div class="progress-line" id="progressBar"></div>

<div class="deck" id="deckContainer">
{slides_html}
</div>

<div class="deck-controls">
    <button class="ctrl-btn" onclick="prevSlide()" title="Previous (Left Arrow)">◀</button>
    <span class="slide-counter" id="slideCounter">1 / {len(slides)}</span>
    <button class="ctrl-btn" onclick="nextSlide()" title="Next (Right Arrow / Space)">▶</button>
    <button class="ctrl-btn" onclick="toggleFullScreen()" title="Toggle Fullscreen (F)">⛶</button>
</div>

<script>
    const slides = document.querySelectorAll('.slide');
    const totalSlides = slides.length;
    let currentIndex = 0;

    function showSlide(index) {{
        if (index < 0) index = 0;
        if (index >= totalSlides) index = totalSlides - 1;
        currentIndex = index;

        slides.forEach((s, idx) => {{
            if (idx === currentIndex) {{
                s.classList.add('active');
            }} else {{
                s.classList.remove('active');
            }}
        }});

        document.getElementById('slideCounter').innerText = `${{currentIndex + 1}} / ${{totalSlides}}`;
        document.getElementById('progressBar').style.width = `${{((currentIndex + 1) / totalSlides) * 100}}%`;
    }}

    function nextSlide() {{
        showSlide(currentIndex + 1);
    }}

    function prevSlide() {{
        showSlide(currentIndex - 1);
    }}

    function goToSlide(index) {{
        showSlide(index);
    }}

    function toggleFullScreen() {{
        if (!document.fullscreenElement) {{
            document.documentElement.requestFullscreen().catch(() => {{}});
        }} else {{
            if (document.exitFullscreen) {{
                document.exitFullscreen();
            }}
        }}
    }}

    window.addEventListener('keydown', (e) => {{
        if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown' || e.key === 'Enter') {{
            e.preventDefault();
            nextSlide();
        }} else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || e.key === 'Backspace') {{
            e.preventDefault();
            prevSlide();
        }} else if (e.key === 'f' || e.key === 'F') {{
            toggleFullScreen();
        }} else if (e.key === 'Home') {{
            showSlide(0);
        }} else if (e.key === 'End') {{
            showSlide(totalSlides - 1);
        }}
    }});

    // Initialize
    showSlide(0);
</script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f_out:
        f_out.write(full_html)
    print(f"[+] Keynote Presentation Slide Deck generated at '{output_path}'!")



def generate_dramatic_keynote_2(chat_title, start_date, end_date, total_days,
                                total_msgs, total_reactions, peak_day, peak_hour,
                                all_users, msg_counts, reactions_received, reactions_given,
                                reply_counts, latencies_active, user_banger_count,
                                messages_with_reactions, user_plat_banger_count,
                                brilliant_one_liners, solo_flow_states, group_flow_states,
                                unreacted_flops, imbalances, mutual_duos, signature_words,
                                user_magnum_opus, awards, media_counts,
                                conversation_starters, conversation_killers, left_on_read,
                                self_reactions, night_owls, early_birds, actor_emoji_types,
                                emoji_counts, output_path):
    """Generate Version 2: Dramatic keynote presentation with progressive step-by-step reveals, suspense mystery boxes, fun facts, and confetti."""
    vol_sorted = msg_counts.most_common()
    rx_sorted = reactions_received.most_common()

    awards_part_1 = [
        ("🏆 The Standup Headliner", "Seán MacDiarmada", "Most bangers in chat history (59 with 4+ reactions) & All-time record 8 reactions!"),
        ("⚡ Supersonic Speed Demon", "Abyan", "Fastest median response time in chat history (14.4 seconds) — never lets a message hang!"),
        ("🦉 The 3 AM Goblin", "Abyan", "7.2% of all messages sent in the dead of night between midnight and 5:00 AM!"),
        ("👑 The Activity Sovereign", "Hannah Irvine", "Absolute bedrock of the chat: 3,913 messages, 2,390 replies, 1,691 reactions given, 206 reels!"),
        ("😂 The Laughing Gas Award", "Darren Kelly", "Group chat giggler #1: sent 130 messages laughing (6.1% of everything he sends)!"),
        ("🎯 The Ruthless Minimalist", "Sophia", "Hit 7 reactions on just 4 words ('Who wants to go?'), #2 fastest responder (16.1s)!"),
        ("🔥 The Flow State Virtuoso", "Joe", "Produced the greatest solo comedy run in chat history: solid bar -> crunchy -> chewy -> etc.!"),
    ]

    awards_part_2 = [
        ("📜 Shakespeare Copypasta King", "Ben Richardson", "Authored the longest single message (944 characters) & held the chat hostage with 14 in a row!"),
        ("⚔️ The Feared Enforcer", "Clodagh🌻", "Mandatory attendance enforcer with an elite 34.1% reaction hit rate and 7-reaction banger!"),
        ("🎩 The Formal Diplomat", "Seán Corry", "Author of 'FORMAL NOTICE OF RE-ENTRY' & Hollywood Handshakes capstone with 36% hit rate!"),
        ("🌅 Early Morning Pogchamp", "ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ", "#1 early bird (10.2% sent 6-10 AM) & single-handedly kept 'poggers' alive with 16 invocations!"),
        ("🙏 The Gracious Host", "Darius Rezania", "Held court with heartfelt post-event thank-you speeches (6 reactions) and 12-message monologues!"),
        ("📈 The Precision Investor", "Aaron Flynn", "Highest reaction hit rate in chat history: 42.9% of his messages receive reactions! Zero wasted words."),
        ("🤝 The Generous Philanthropist", "Pauric Mc Bride", "Gives 1.06 reactions for every message he sends — purely here to support the squad!"),
    ]

    esc = html.escape
    slides = []

    # =============================================================================
    # SLIDE 1: TITLE SLIDE
    # =============================================================================
    slides.append(f"""
    <div class="slide-content title-slide">
        <div class="kicker">ANNUAL CHAT RETROSPECTIVE • 2026</div>
        <h1 class="main-title">{esc(chat_title)}</h1>
        <p class="subtitle">The Official Interactive Keynote &amp; Dossier</p>
        <div class="meta-tag-strip reveal-item" data-step="1">
            <span class="tag">18,061 Messages</span>
            <span class="tag">7,937 Reactions</span>
            <span class="tag">290 Days of Banter</span>
            <span class="tag">Zero Filter</span>
        </div>
        <div class="fun-fact-box reveal-item" data-step="2">
            💡 <strong>Fun Fact:</strong> This group chat produced an average of 62.3 messages every single day of 2026 without a single day of total silence.
        </div>
        <div class="nav-hint">Press <kbd>→</kbd> or <kbd>Space</kbd> to begin presentation</div>
    </div>
    """)

    # =============================================================================
    # SLIDE 2: THE DOSSIER (VITAL SIGNS - STEP REVEAL)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 01</div>
            <h2>The Dossier: Vital Signs</h2>
            <p class="slide-desc">High-level statistics spanning January 19 to November 6, 2026.</p>
        </div>
        <div class="stats-grid">
            <div class="stat-card reveal-item" data-step="1">
                <span class="number">{total_msgs:,}</span>
                <span class="label">Total Messages</span>
                <span class="detail">{total_msgs/total_days:.1f} messages per day</span>
            </div>
            <div class="stat-card highlight reveal-item" data-step="2">
                <span class="number">{sum(reactions_received.values()):,}</span>
                <span class="label">Reactions Given</span>
                <span class="detail">0.44 reactions per message average</span>
            </div>
            <div class="stat-card accent reveal-item" data-step="3">
                <span class="number">{peak_day[1]:,}</span>
                <span class="label">Record-Breaking Day</span>
                <span class="detail">{esc(peak_day[0])}</span>
            </div>
            <div class="stat-card reveal-item" data-step="4">
                <span class="number">{peak_hour[0]:02d}:00</span>
                <span class="label">Peak Unhinged Hour</span>
                <span class="detail">{peak_hour[1]:,} messages sent at lunch (1-2 PM)</span>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> On Thursday, October 15th, a new message was posted every 2 minutes for 24 continuous hours. Nobody got any college work done.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 3: VOLUME LEADERBOARD (STEP-BY-STEP REVEAL COUNTDOWN)
    # =============================================================================
    vol_sorted = msg_counts.most_common()
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 02</div>
            <h2>Volume Leaderboard: The Chatterboxes</h2>
            <p class="slide-desc">Ranking by total raw messages sent in chat history.</p>
        </div>
        <div class="leaderboard-reveal-grid">
            <div class="reveal-item card tier-box" data-step="1">
                <span class="tier-label">THE FOUNDATION</span>
                <div class="tier-row"><span>#5 Darius Rezania</span><strong>{vol_sorted[4][1]:,} msgs</strong></div>
                <div class="tier-row"><span>#4 Seán MacDiarmada</span><strong>{vol_sorted[3][1]:,} msgs</strong></div>
            </div>
            <div class="reveal-item card tier-box" data-step="2">
                <span class="tier-label">THE CONTENDERS</span>
                <div class="tier-row"><span>#3 Sophia</span><strong>{vol_sorted[2][1]:,} msgs</strong></div>
                <div class="tier-row"><span>#2 Darren Kelly</span><strong>{vol_sorted[1][1]:,} msgs</strong></div>
            </div>
            <div class="reveal-item mystery-card-container" data-step="3">
                <div class="mystery-box">🔒 PRESS SPACE FOR #1 CROWNING CHATTERBOX</div>
                <div class="winner-reveal-banner">
                    <div class="winner-trophy">👑 #1 CHATTERBOX OF 2026</div>
                    <div class="winner-name-huge">HANNAH IRVINE</div>
                    <div class="winner-stat-huge">{vol_sorted[0][1]:,} MESSAGES SENT ({vol_sorted[0][1]/total_msgs*100:.1f}% OF THE ENTIRE CHAT)</div>
                </div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Hannah alone accounts for 21.7% of every word ever uttered in this group chat. If Hannah was muted for two weeks, group chat GDP would immediately crash by a quarter.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 4: THE CLOUT LEADERBOARD (REACTIONS RECEIVED REVEAL)
    # =============================================================================
    rx_sorted = reactions_received.most_common()
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 03</div>
            <h2>The Clout Index: Reactions Received</h2>
            <p class="slide-desc">Who accumulated the most validation and love from the squad?</p>
        </div>
        <div class="leaderboard-reveal-grid">
            <div class="reveal-item card tier-box" data-step="1">
                <span class="tier-label">THE HIGH EARNERS</span>
                <div class="tier-row"><span>#6 Joe</span><strong>{rx_sorted[5][1]:,} rx</strong></div>
                <div class="tier-row"><span>#5 Abyan</span><strong>{rx_sorted[4][1]:,} rx</strong></div>
                <div class="tier-row"><span>#4 Clodagh🌻</span><strong>{rx_sorted[3][1]:,} rx</strong></div>
            </div>
            <div class="reveal-item card tier-box highlight-box" data-step="2">
                <span class="tier-label">THE TRAGIC MILESTONE</span>
                <div class="tier-row"><span>#3 Darren Kelly</span><strong style="color: #ec4899;">999 Reactions!</strong></div>
                <div class="sub-caption">Literally 1 reaction away from the four-digit promised land.</div>
            </div>
            <div class="reveal-item card tier-box" data-step="3">
                <span class="tier-label">THE RUNNER-UP</span>
                <div class="tier-row"><span>#2 Seán MacDiarmada</span><strong>1,138 Reactions</strong></div>
                <div class="sub-caption">Accumulated over 1,100 reactions on half the messages sent.</div>
            </div>
            <div class="reveal-item mystery-card-container" data-step="4">
                <div class="mystery-box">🔒 PRESS SPACE FOR #1 CLOUT SOVEREIGN</div>
                <div class="winner-reveal-banner gold-bg">
                    <div class="winner-trophy">👑 #1 CLOUT SOVEREIGN</div>
                    <div class="winner-name-huge">HANNAH IRVINE</div>
                    <div class="winner-stat-huge">{rx_sorted[0][1]:,} TOTAL REACTIONS RECEIVED</div>
                </div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> Darren finished the entire 10-month season at exactly 999 reactions. We urge everyone to give Darren one reaction tonight so he can finally rest in peace.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 5: COMEDIC EFFICIENCY (HIT RATE - STEP REVEAL)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 04</div>
            <h2>Comedic Efficiency: The Sniper Hit Rate</h2>
            <p class="slide-desc">Percentage of sent messages that earned at least one reaction (Quality over Quantity).</p>
        </div>
        <div class="grid-3">
            <div class="card metric-box reveal-item" data-step="1">
                <div class="metric-top"><span class="rank-pill">Tier 3</span><span class="metric-val">27%</span></div>
                <div class="metric-user">The High-Volume Crew</div>
                <div class="metric-sub">Hannah (26.6%) &amp; Darren (27.7%) • Spray &amp; Pray</div>
            </div>
            <div class="card metric-box reveal-item" data-step="2">
                <div class="metric-top"><span class="rank-pill">Tier 2</span><span class="metric-val">34-36%</span></div>
                <div class="metric-user">The Precision Squad</div>
                <div class="metric-sub">Clodagh (34.1%), Seán M (34.1%), Abyan (35.9%), Seán C (36.0%)</div>
            </div>
            <div class="card metric-box highlight-card reveal-item" data-step="3">
                <div class="metric-top"><span class="rank-pill gold-pill">👑 #1 SNIPER</span><span class="metric-val" style="color: #f59e0b;">42.9%</span></div>
                <div class="metric-user">Aaron Flynn</div>
                <div class="metric-sub">42.9% Reaction Hit Rate • Zero Wasted Keystrokes</div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Aaron Flynn sent only 91 messages all year, but almost half of them received reactions. He logs on, drops a certified zinger, and vanishes into the Irish fog.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 6: SPEED DEMONS (STEP REVEAL COUNTDOWN)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 05</div>
            <h2>Speed Demons: Fastest Draw in the West</h2>
            <p class="slide-desc">Median active response latency when another person finishes speaking.</p>
        </div>
        <div class="speed-reveal-container">
            <div class="card speed-step-card reveal-item" data-step="1">
                <span class="badge sloth-badge">🦥 THE PATIENT SLOTH AWARD</span>
                <div class="speed-content-wrap">
                    <strong>Aaron Flynn</strong> takes a median of <strong style="color: #f87171;">79.7 seconds</strong> to reply.
                </div>
                <span class="speed-desc">"I'll reply when I'm good and ready."</span>
            </div>
            <div class="card speed-step-card reveal-item" data-step="2">
                <span class="badge">🚶 THE STEADY MIDFIELD</span>
                <div class="speed-content-wrap">
                    Daniel (39.9s) • Seán M (22.5s) • Hannah (22.4s) • Darren (22.1s)
                </div>
                <span class="speed-desc">Standard active conversational pace</span>
            </div>
            <div class="card speed-step-card reveal-item" data-step="3">
                <span class="badge silver-badge">🥈 THE RAPID PODIUM</span>
                <div class="speed-content-wrap">
                    🥉 <strong>Seán Corry</strong>: 17.0s • 🥈 <strong>Sophia</strong>: 16.1s
                </div>
                <span class="speed-desc">Answering before the notification sound finishes</span>
            </div>
            <div class="card speed-step-card highlight-box reveal-item" data-step="4">
                <span class="badge gold-badge">⚡ THE SUPERSONIC CHAMPION</span>
                <div class="speed-content-wrap">
                    🥇 <strong>ABYAN: 14.4 SECONDS MEDIAN LATENCY!</strong>
                </div>
                <span class="speed-desc">Fastest thumbs in group chat history</span>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> Abyan's median response time is 14.4 seconds. That means before you've even locked your phone screen, Abyan has already read your text, formulated a rebuttal, and hit send.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 7: STARTERS VS ROOM SILENCERS (STEP REVEAL)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 06</div>
            <h2>The Defibrillators vs The Room Silencers</h2>
            <p class="slide-desc">Tracking conversational dynamics around 2+ hour dead silences.</p>
        </div>
        <div class="grid-3">
            <div class="card reveal-item" data-step="1">
                <h3>🚒 The Defibrillators</h3>
                <p class="subtext">Broke an awkward >2h silence</p>
                <ul class="styled-list">
                    <li><strong>Hannah Irvine</strong>: 105 times</li>
                    <li><strong>Abyan</strong>: 93 times</li>
                    <li><strong>Darren Kelly</strong>: 52 times</li>
                </ul>
            </div>
            <div class="card reveal-item" data-step="2">
                <h3>💀 The Room Silencers</h3>
                <p class="subtext">Sent the last message before silence</p>
                <ul class="styled-list">
                    <li><strong>Hannah Irvine</strong>: 109 times</li>
                    <li><strong>Abyan</strong>: 83 times</li>
                    <li><strong>Darren Kelly</strong>: 54 times</li>
                </ul>
            </div>
            <div class="card reveal-item" data-step="3">
                <h3>🕳️ Left in The Void</h3>
                <p class="subtext">0 reactions followed by >2h silence</p>
                <ul class="styled-list">
                    <li><strong>Hannah Irvine</strong>: 43 times</li>
                    <li><strong>Abyan</strong>: 33 times</li>
                    <li><strong>ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong>: 28 times</li>
                </ul>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Hannah and Abyan started 65% of all conversations in this chat, but also killed 60% of them. They are the twin deities of life and death in Lemmings &amp; Gooners.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 8: THE MEDIA VAULT & REEL JUNKIES
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 07</div>
            <h2>The Media Vault &amp; Reel Junkies</h2>
            <p class="slide-desc">Who sends the photos, who bombards the group with Instagram Reels?</p>
        </div>
        <div class="grid-2">
            <div class="card media-box reveal-item" data-step="1">
                <h3>📸 The Paparazzi (Photos Sent)</h3>
                <p class="subtext">529 total photos dropped in chat history</p>
                <ul class="styled-list">
                    <li><strong>Hannah Irvine</strong>: 136 photos</li>
                    <li><strong>Seán MacDiarmada</strong>: 55 photos</li>
                    <li><strong>Ben Richardson</strong>: 52 photos</li>
                    <li><strong>Darius Rezania</strong>: 49 photos</li>
                </ul>
            </div>
            <div class="card media-box highlight-box reveal-item" data-step="2">
                <h3>📲 The Reel Addicts (Shared Posts)</h3>
                <p class="subtext">504 shared Instagram posts/reels forwarded</p>
                <ul class="styled-list">
                    <li><strong>Hannah Irvine</strong>: 206 shared reels (41% of all reels!)</li>
                    <li><strong>Abyan</strong>: 74 shared reels</li>
                    <li><strong>Darren Kelly</strong>: 59 shared reels</li>
                    <li><strong>Seán MacDiarmada</strong>: 45 shared reels</li>
                </ul>
            </div>
        </div>
        <div class="callout-card reveal-item" data-step="3">
            🎙️ <strong>The Solitary Voice Note Record:</strong> Across 18,061 messages in 10 months, exactly <strong>TWO voice notes</strong> were ever sent. One by Hannah, one by Ben. Nobody wants to hear our actual voices.
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Statistically, for every 5 Instagram reels you have received on your phone in 2026, 2 of them were forwarded by Hannah Irvine.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 9: THE CRICKETS AWARD (THE LONGEST UNREACTED ESSAYS)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 08</div>
            <h2>The Crickets Award: Poured Their Soul Out</h2>
            <p class="slide-desc">The longest essays and manifestos that received literally ZERO reactions.</p>
        </div>
        <div class="cricket-container">
            <div class="card cricket-card reveal-item" data-step="1">
                <div class="cricket-header">
                    <strong>🥉 #3 Ben Richardson</strong>
                    <span class="badge rx-zero">0 Reactions • 444 characters</span>
                </div>
                <p class="cricket-quote">“I wanna hear someone say that just one conversation with someone changed their lives fundamentally. I wanna hear that so bad...”</p>
                <div class="cricket-date">Result: Absolute Crickets 🦗</div>
            </div>
            <div class="card cricket-card reveal-item" data-step="2">
                <div class="cricket-header">
                    <strong>🥈 #2 ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong>
                    <span class="badge rx-zero">0 Reactions • 489 characters</span>
                </div>
                <p class="cricket-quote">“Normally with wodden floors im a big fan of a dark grey, just makes the woods pop. But that needs most things in the room to have some form of grey...”</p>
                <div class="cricket-date">Result: Unsolicited Interior Design Masterclass met with Dead Silence 🦗</div>
            </div>
            <div class="card cricket-card highlight-cricket reveal-item" data-step="3">
                <div class="cricket-header">
                    <strong>👑 #1 BEN RICHARDSON</strong>
                    <span class="badge rx-zero">0 Reactions • 944 characters (173 WORDS!)</span>
                </div>
                <p class="cricket-quote">“It’s true “tall brunette your so pretty” “Bess boy in quarter zip I’ll make out with you after lecture “ NO STOP STOP STOP. IN A CROWDED LECTURE HALL WITH 200 PEOPLE I SIT THERE AND HOPE YOU WALK THROUGH THE DOOR...”</p>
                <div class="cricket-date">Result: 173 words with Caps-Lock enabled in class $\rightarrow$ Zero Emotional Support 🦗</div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Daniel gave detailed architectural flooring advice and Ben confessed his lecture hall heartbreak. Neither received a single thumb, heart, or prayer.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 10: CROWNING ONE-LINERS (THE SNIPERS - STEP REVEAL)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 09</div>
            <h2>Crowning One-Liners: The Comedy Snipers</h2>
            <p class="slide-desc">Standalone bangers dropped into an otherwise normal chat with zero surrounding liked messages.</p>
        </div>
        <div class="grid-2">
            <div class="card sniper-card reveal-item" data-step="1">
                <div class="sniper-top"><span class="badge rx-badge">7 Reactions</span><span class="sniper-date">Nov 1, 2026</span></div>
                <p class="sniper-text">“I’m less nice than Daniel, mandatory attendance for all of you”</p>
                <div class="sniper-footer"><strong>Clodagh🌻</strong><span>❤ ❤ ❤ ❤ ❤ ❤ ❤</span></div>
            </div>
            <div class="card sniper-card reveal-item" data-step="2">
                <div class="sniper-top"><span class="badge rx-badge">7 Reactions</span><span class="sniper-date">Oct 16, 2026</span></div>
                <p class="sniper-text">“Can I do my capstone on Hollywood handshakes”</p>
                <div class="sniper-footer"><strong>Seán Corry</strong><span>❤ ❤ ❤ ❤ ❤ ❤ ❤</span></div>
            </div>
            <div class="card sniper-card reveal-item" data-step="3">
                <div class="sniper-top"><span class="badge rx-badge">7 Reactions</span><span class="sniper-date">Feb 13, 2026</span></div>
                <p class="sniper-text">“Who wants to go?”</p>
                <div class="sniper-footer"><strong>Sophia</strong><span>❤ ❤ ❤ ❤ ❤ ❤ ❤ (4 Words = 7 Likes!)</span></div>
            </div>
            <div class="card sniper-card highlight-card reveal-item" data-step="4">
                <div class="sniper-top"><span class="badge rx-badge">🏆 8 Reactions (CHAT RECORD)</span><span class="sniper-date">Feb 12, 2026</span></div>
                <p class="sniper-text">“hey guys i would like to invite you (yes you) to swords on the 21st of February to celebrate my 21st birthday in my abode (mandatory attendance)...”</p>
                <div class="sniper-footer"><strong>Seán MacDiarmada</strong><span>❤ ❤ ❤ ❤ ❤ ❤ ❤ ❤</span></div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> Sophia achieved a ratio of 1.75 reactions per word typed on 'Who wants to go?'. That is the highest return on investment in chat history.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 11: SOLO FLOW STATE (COOKING IN THE ZONE - STEP REVEAL)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 10</div>
            <h2>Solo Flow State: Cooking in the Zone</h2>
            <p class="slide-desc">When one person enters an unbroken manic comedy heater completely alone.</p>
        </div>
        <div class="card flow-showcase">
            <div class="flow-meta">
                <h3>👑 Joe's 30-Second Symphony</h3>
                <span>July 15, 2026 • 20 Total Reactions in half a minute</span>
            </div>
            <div class="bubble-stack">
                <div class="chat-bubble joe-bubble reveal-item" data-step="1"><span class="rx-pill">4 rx</span> “i like the solid bar”</div>
                <div class="chat-bubble joe-bubble reveal-item" data-step="2"><span class="rx-pill">6 rx 🔥</span> “crunchy”</div>
                <div class="chat-bubble joe-bubble reveal-item" data-step="3"><span class="rx-pill">5 rx</span> “chewy”</div>
                <div class="chat-bubble joe-bubble reveal-item" data-step="4"><span class="rx-pill">5 rx</span> “etc.”</div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> Joe collected 20 reactions across 4 messages in under 30 seconds. That is an efficiency of 0.66 reactions per second.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 12: GROUP FLOW STATE #1 (GOONER PARLIAMENTARY DEBATE - LINE BY LINE)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 1</div>
            <h2>The Parliamentary Gooner Debate</h2>
            <p class="slide-desc">October 20, 2026 • 4 Bangers in 6 Minutes (20 Total Reactions)</p>
        </div>
        <div class="chat-dialogue">
            <div class="chat-row left reveal-item" data-step="1">
                <span class="avatar">Joe</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “what's unethical about sucking your own dick”</div>
            </div>
            <div class="chat-row right reveal-item" data-step="2">
                <span class="avatar">ᴅᴀɴɪᴇʟ</span>
                <div class="bubble"><span class="rx-tag">4 rx</span> “Whats unethical about trying to suck it yourself?”</div>
            </div>
            <div class="chat-row left reveal-item" data-step="3">
                <span class="avatar">Seán C</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> ““Mr speaker I am a gooner” ahh”</div>
            </div>
            <div class="chat-row right reveal-item" data-step="4">
                <span class="avatar">Hannah</span>
                <div class="bubble"><span class="rx-tag">6 rx</span> “Of he can do this for himself imagine what h could do for me”</div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> This parliamentary debate lasted exactly 6 minutes, involved 4 committee members, and achieved 100% bipartisan agreement.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 13: GROUP FLOW STATE #2 (TEMU DARREN & TWO-LINE SLANDER)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 2</div>
            <h2>Temu Darren &amp; Two-Line Slander</h2>
            <p class="slide-desc">Rapid-fire comedic execution across July &amp; October 2026.</p>
        </div>
        <div class="grid-2">
            <div class="card riff-card">
                <h3>Riff A: The Temu Incident (July 17)</h3>
                <div class="dialogue-mini">
                    <div class="msg reveal-item" data-step="1"><strong>Darren Kelly</strong> [4 rx]: “The goat???”</div>
                    <div class="msg reply reveal-item" data-step="2"><strong>Sophia</strong> [4 rx]: “David Kenny aka temu Darren Kelly”</div>
                </div>
            </div>
            <div class="card riff-card">
                <h3>Riff B: Acronym Slander (October 5)</h3>
                <div class="dialogue-mini">
                    <div class="msg reveal-item" data-step="3"><strong>Darren Kelly</strong> [5 rx]: “The s in smacks stands for si”</div>
                    <div class="msg reply reveal-item" data-step="4"><strong>Seán MacDiarmada</strong> [6 rx]: “the o in joe stands for obese”</div>
                </div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="5">
            💡 <strong>Fun Fact:</strong> Sophia calling David Kenny 'Temu Darren Kelly' was unprovoked and devastating. And Seán's rebuttal on Joe's acronym was pure violence.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 14: GROUP FLOW STATE #3 (FATHERHOOD & THE XENOMORPH)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">GROUP FLOW STATE • PART 3</div>
            <h2>Fatherhood &amp; The Xenomorph</h2>
            <p class="slide-desc">August 27, 2026 • From wholesome family warmth to pure chaos in 64 seconds.</p>
        </div>
        <div class="chat-dialogue">
            <div class="chat-row left reveal-item" data-step="1">
                <span class="avatar">Ben</span>
                <div class="bubble wide"><span class="rx-tag">6 rx</span> “So beautiful and amazing and I would love to be a father and be blessed to have a lovely wife and child... cuddle in bed in the morning as a family...”</div>
            </div>
            <div class="chat-row left reveal-item" data-step="2">
                <span class="avatar">Ben</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “Pffffff Alri ig”</div>
            </div>
            <div class="chat-row right reveal-item" data-step="3">
                <span class="avatar">Seán M</span>
                <div class="bubble"><span class="rx-tag">5 rx</span> “"are you a xenomorph because I'd let you burst on my chest" - Darren”</div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> The group shifted from wholesome parenting philosophy to extraterrestrial chest-burster romance in 64 seconds flat.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 15: CHEMISTRY & THE SIMP MATRIX
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 11</div>
            <h2>Chemistry &amp; The Simp Matrix</h2>
            <p class="slide-desc">Mutual power duos versus the biggest one-sided reaction deficits.</p>
        </div>
        <div class="grid-2">
            <div class="card reveal-item" data-step="1">
                <h3>💞 Top Mutual Duos</h3>
                <p class="subtext">Equal exchange of love and hype</p>
                <ul class="styled-list">
                    <li><strong>Joe &amp; Hannah</strong>: 500 mutual reactions</li>
                    <li><strong>Hannah &amp; Seán M</strong>: 395 mutual reactions</li>
                    <li><strong>Daniel &amp; Hannah</strong>: 363 mutual reactions</li>
                </ul>
            </div>
            <div class="card reveal-item" data-step="2">
                <h3>💔 The Simp Matrix (Unrequited Hype)</h3>
                <div class="simp-card">
                    <div class="simp-pair"><strong>Daniel</strong> ➔ <strong>Hannah</strong></div>
                    <div class="simp-detail">Gave 268 rx • Received only 95 back (Net -173 💔)</div>
                </div>
                <div class="simp-card">
                    <div class="simp-pair"><strong>Hannah</strong> ➔ <strong>Seán M</strong></div>
                    <div class="simp-detail">Gave 274 rx • Received only 121 back (Net -153 💔)</div>
                </div>
                <div class="simp-card">
                    <div class="simp-pair"><strong>Joe</strong> ➔ <strong>Seán M</strong></div>
                    <div class="simp-detail">Gave 190 rx • Received only 45 back (Net -145 💔)</div>
                </div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="3">
            💡 <strong>Fun Fact:</strong> Daniel's reaction investment in Hannah Irvine yielded a -65% return on equity. Financial analysts recommend cutting losses immediately.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 16: THE NARCISSUS TROPHY (SELF-REACTIONS)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 12</div>
            <h2>The Narcissus Trophy: Self-Reactions</h2>
            <p class="slide-desc">Caught in 4K reacting with emojis to their own sent messages.</p>
        </div>
        <div class="card narcissus-box">
            <div class="trophy-icon">🪞</div>
            <div class="podium-row">
                <div class="podium-item reveal-item" data-step="1"><span class="badge self-badge">4x</span> <strong>Hannah Irvine</strong></div>
                <div class="podium-item reveal-item" data-step="2"><span class="badge self-badge">6x</span> <strong>Abyan</strong></div>
                <div class="podium-item highlight-podium reveal-item" data-step="3"><span class="badge gold-badge">9x 👑</span> <strong>Seán MacDiarmada</strong></div>
                <div class="podium-item highlight-podium reveal-item" data-step="3"><span class="badge gold-badge">9x 👑</span> <strong>ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong></div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Nine times this year, Seán and Daniel looked at their own text, chuckled to themselves, and double-tapped their own message. Self-love is essential.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 17: SLEEP SCHEDULES: 3 AM GOBLINS VS EARLY BIRDS
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 13</div>
            <h2>Circadian Rhythms: Sleep Schedules</h2>
            <p class="slide-desc">Who texts at ungodly hours, and who is awake before the sun?</p>
        </div>
        <div class="grid-2">
            <div class="card time-card owl-bg reveal-item" data-step="1">
                <h3>🦉 The 3 AM Goblins (Midnight – 5 AM)</h3>
                <ul class="styled-list">
                    <li><strong>Abyan</strong>: 74 msgs (7.2% late night!)</li>
                    <li><strong>Joe</strong>: 67 msgs (6.5%)</li>
                    <li><strong>Aaron Flynn</strong>: 5 msgs (5.5%)</li>
                    <li><strong>Seán MacDiarmada</strong>: 94 msgs (5.2%)</li>
                </ul>
            </div>
            <div class="card time-card bird-bg reveal-item" data-step="2">
                <h3>🌅 The Early Birds (6 AM – 10 AM)</h3>
                <ul class="styled-list">
                    <li><strong>ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong>: 87 msgs (10.2% before 10 AM!)</li>
                    <li><strong>Ben Richardson</strong>: 113 msgs (7.7%)</li>
                    <li><strong>Darius Rezania</strong>: 118 msgs (7.5%)</li>
                    <li><strong>Clodagh🌻</strong>: Only 13 msgs (1.1% — Comatose)</li>
                </ul>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="3">
            💡 <strong>Fun Fact:</strong> Abyan and Joe operate on Tokyo time. Meanwhile, Daniel is awake at 7 AM saying poggers, and Clodagh is medically comatose until midday.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 18: SIGNATURE VOCABULARY
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 14</div>
            <h2>Signature Vocabulary: Linguistic Fingerprints</h2>
            <p class="slide-desc">Cleaned distinctive words used disproportionately by each person.</p>
        </div>
        <div class="grid-2">
            <div class="card vocab-box reveal-item" data-step="1">
                <strong>ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong>
                <p>poggers (16x) • dargin (10x) • yargin (14x) • funtion (9x)</p>
            </div>
            <div class="card vocab-box reveal-item" data-step="1">
                <strong>Darren Kelly</strong>
                <p>climbing (26x) • coz (15x) • ibec (11x) • gna (9x)</p>
            </div>
            <div class="card vocab-box reveal-item" data-step="2">
                <strong>Joe</strong>
                <p>toastie (6x) • pulling (5x) • munster (4x) • kobe (3x)</p>
            </div>
            <div class="card vocab-box reveal-item" data-step="2">
                <strong>Ben Richardson</strong>
                <p>chaperone (10x) • shoutout (10x) • gunna (9x) • spell (4x)</p>
            </div>
            <div class="card vocab-box reveal-item" data-step="3">
                <strong>Abyan</strong>
                <p>hypergamy (4x) • acai (4x) • cuz (12x) • terms (5x)</p>
            </div>
            <div class="card vocab-box reveal-item" data-step="3">
                <strong>Seán Corry</strong>
                <p>amphitheater (4x) • ole (6x) • daz (4x) • ahah (3x)</p>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Daniel Cairney is single-handedly responsible for 100% of global 'dargin' and 'yargin' usage in 2026.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 19: EMOJI CONNOISSEURS & WILD REACTIONS
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 15</div>
            <h2>Emoji Connoisseurs &amp; Wild Reactions</h2>
            <p class="slide-desc">Over 155 distinct emojis were used as reactions in this chat.</p>
        </div>
        <div class="grid-2">
            <div class="card reveal-item" data-step="1">
                <h3>👑 The Standard Emoji Hierarchy</h3>
                <div class="emoji-stat">❤ Heart: <strong>6,627x</strong></div>
                <div class="emoji-stat">🔥 Fire: <strong>112x</strong></div>
                <div class="emoji-stat">😂 Laugh: <strong>93x</strong></div>
                <div class="emoji-stat">😢 Cry: <strong>86x</strong></div>
            </div>
            <div class="card highlight-box reveal-item" data-step="2">
                <h3>🧌 The Unhinged Emoji Collection</h3>
                <div class="emoji-stat">🐐 Goat: <strong>61x</strong></div>
                <div class="emoji-stat">🇮🇱 Israeli Flag: <strong>78x</strong></div>
                <div class="emoji-stat">🗜️ Metal Clamp: <strong>6x</strong></div>
                <div class="emoji-stat">🧼 Bar of Soap: <strong>4x</strong></div>
                <div class="emoji-stat">⚰️ Coffin: <strong>2x</strong></div>
                <div class="emoji-stat">🔪 Kitchen Knife: <strong>2x</strong></div>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="3">
            💡 <strong>Fun Fact:</strong> There are 155 different emojis in our reaction archive. At least 6 messages this year made someone feel compelled to react with an industrial C-clamp.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 20: MAGNUM OPUS (PART 1)
    # =============================================================================
    sorted_opus = sorted(user_magnum_opus.items(), key=lambda x: x[1]["rx_count"], reverse=True)
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 16 • PART 1</div>
            <h2>Magnum Opus: Crowning Achievements</h2>
            <p class="slide-desc">Every member's single highest-rated message in group chat history.</p>
        </div>
        <div class="grid-2">
            <div class="card opus-slide-card reveal-item" data-step="1">
                <div class="opus-top"><strong>Seán MacDiarmada</strong><span class="badge rx-badge">8 Reactions</span></div>
                <p class="opus-quote">“hey guys i would like to invite you (yes you) to swords on the 21st of February to celebrate my 21st birthday in my abode (mandatory attendance)...”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="1">
                <div class="opus-top"><strong>Sophia</strong><span class="badge rx-badge">7 Reactions</span></div>
                <p class="opus-quote">“Who wants to go?”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="2">
                <div class="opus-top"><strong>Clodagh🌻</strong><span class="badge rx-badge">7 Reactions</span></div>
                <p class="opus-quote">“I’m less nice than Daniel, mandatory attendance for all of you”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="2">
                <div class="opus-top"><strong>Seán Corry</strong><span class="badge rx-badge">7 Reactions</span></div>
                <p class="opus-quote">“Can I do my capstone on Hollywood handshakes”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="3">
                <div class="opus-top"><strong>Hannah Irvine</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“#justbenthings”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="3">
                <div class="opus-top"><strong>Darren Kelly</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“Darren Kelly is interning this summer”</p>
            </div>
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 21: MAGNUM OPUS (PART 2)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 16 • PART 2</div>
            <h2>Magnum Opus: Crowning Achievements</h2>
            <p class="slide-desc">Every member's single highest-rated message in group chat history.</p>
        </div>
        <div class="grid-2">
            <div class="card opus-slide-card reveal-item" data-step="1">
                <div class="opus-top"><strong>Abyan</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“Yesterday in Bank Indonesia: The IMF visited and gave me a Malaysian themed frige magnet”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="1">
                <div class="opus-top"><strong>Joe</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“i will 1 v 1 rootbeer in crossclimb”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="2">
                <div class="opus-top"><strong>Ben Richardson</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“So beautiful and amazing and I would love to be a father and be blessed to have a lovely wife and child...”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="2">
                <div class="opus-top"><strong>ᴅᴀɴɪᴇʟ ᴄᴀɪʀɴᴇʏ</strong><span class="badge rx-badge">5 Reactions</span></div>
                <p class="opus-quote">“Legendary”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="3">
                <div class="opus-top"><strong>Aaron Flynn</strong><span class="badge rx-badge">5 Reactions</span></div>
                <p class="opus-quote">“hello all, i will be leaving ireland on the 18th; might a function on the 16th be the move 🤔”</p>
            </div>
            <div class="card opus-slide-card reveal-item" data-step="3">
                <div class="opus-top"><strong>Darius Rezania</strong><span class="badge rx-badge">6 Reactions</span></div>
                <p class="opus-quote">“Thank yous for coming yesterday hope yous all enjoyed it like I did, and I'll see yous all again soon for poker...”</p>
            </div>
        </div>
        <div class="fun-fact-box reveal-item" data-step="4">
            💡 <strong>Fun Fact:</strong> Abyan's crowning group chat legacy is receiving a souvenir fridge magnet from the International Monetary Fund.
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 22: THE SUPERLATIVES CEREMONY (PART 1 - DRAMATIC WINNER REVEALS)
    # =============================================================================
    def render_award_item(step, title, winner, reason):
        return f"""
        <div class="card award-reveal-card reveal-item" data-step="{step}" data-winner="{esc(winner)}">
            <div class="award-trophy">{esc(title)}</div>
            <div class="award-setup">{esc(reason)}</div>
            <div class="mystery-box">🔒 REVEAL WINNER</div>
            <div class="winner-badge-revealed">👑 {esc(winner)}</div>
        </div>
        """

    awards_html_1 = "".join([render_award_item(i+1, t, w, r) for i, (t, w, r) in enumerate(awards_part_1)])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 17 • PART 1</div>
            <h2>The Official Superlatives Ceremony</h2>
            <p class="slide-desc">Data-backed accolades awarded to each participant (Press Space to reveal each winner!).</p>
        </div>
        <div class="awards-reveal-grid">
            {awards_html_1}
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 23: THE SUPERLATIVES CEREMONY (PART 2 - DRAMATIC WINNER REVEALS)
    # =============================================================================
    awards_html_2 = "".join([render_award_item(i+1, t, w, r) for i, (t, w, r) in enumerate(awards_part_2)])

    slides.append(f"""
    <div class="slide-content">
        <div class="slide-header">
            <div class="kicker">SECTION 17 • PART 2</div>
            <h2>The Official Superlatives Ceremony</h2>
            <p class="slide-desc">Data-backed accolades awarded to each participant (Press Space to reveal each winner!).</p>
        </div>
        <div class="awards-reveal-grid">
            {awards_html_2}
        </div>
    </div>
    """)

    # =============================================================================
    # SLIDE 24: FINALE SLIDE (CONFETTI EXPLOSION!)
    # =============================================================================
    slides.append(f"""
    <div class="slide-content finale-slide">
        <div class="kicker">THAT'S A WRAP • 2026</div>
        <h1 class="main-title">Thank You, Lemmings &amp; Gooners.</h1>
        <p class="subtitle">Here's to another 18,000 unhinged messages in 2027.</p>
        <div class="meta-tag-strip reveal-item" data-step="1">
            <span class="tag">Dossier Complete</span>
            <span class="tag">Safe &amp; Sound</span>
            <span class="tag">#eattheSU</span>
        </div>
        <button class="restart-btn" onclick="goToSlide(0)">↺ Replay Presentation</button>
    </div>
    """)

    slides_html = ""
    for idx, s in enumerate(slides):
        active_cls = " active" if idx == 0 else ""
        slides_html += f'<section class="slide{active_cls}" data-slide="{idx}">{s}</section>\n'

    full_html = f"""<!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(chat_title)} - 2026 Interactive Keynote (Version 2)</title>
    <style>
        :root {{
            --bg-main: #0c0d14;
            --bg-card: rgba(26, 29, 46, 0.85);
            --bg-card-hover: rgba(35, 39, 60, 0.95);
            --text-bright: #ffffff;
            --text-sub: #a1adcb;
            --accent-purple: #8b5cf6;
            --accent-pink: #ec4899;
            --accent-cyan: #06b6d4;
            --accent-gold: #f59e0b;
            --accent-green: #10b981;
            --border-glass: rgba(255, 255, 255, 0.1);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-main);
            color: var(--text-bright);
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            overflow: hidden;
            height: 100vh;
            width: 100vw;
            user-select: none;
        }}

        canvas#confettiCanvas {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            pointer-events: none;
            z-index: 999;
        }}

        .deck {{
            position: relative;
            width: 100vw;
            height: 100vh;
            overflow: hidden;
        }}
        .slide {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            opacity: 0;
            pointer-events: none;
            transform: scale(0.97) translateY(15px);
            transition: opacity 0.35s cubic-bezier(0.16, 1, 0.3, 1), transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
            padding: 3rem 4rem 5rem;
        }}
        .slide.active {{
            opacity: 1;
            pointer-events: auto;
            transform: scale(1) translateY(0);
        }}

        .slide-content {{
            max-width: 1150px;
            width: 100%;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}

        /* Reveal System */
        .reveal-item {{
            opacity: 0;
            transform: translateY(18px) scale(0.97);
            filter: blur(5px);
            transition: opacity 0.45s cubic-bezier(0.16, 1, 0.3, 1),
                        transform 0.45s cubic-bezier(0.16, 1, 0.3, 1),
                        filter 0.45s ease;
            pointer-events: none;
        }}
        .reveal-item.revealed {{
            opacity: 1;
            transform: translateY(0) scale(1);
            filter: blur(0);
            pointer-events: auto;
        }}

        /* Mystery Boxes for Awards */
        .mystery-box {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(245, 158, 11, 0.15);
            border: 2px dashed rgba(245, 158, 11, 0.5);
            border-radius: 12px;
            padding: 0.6rem 1.2rem;
            font-size: 0.95rem;
            font-weight: 800;
            color: #f59e0b;
            letter-spacing: 1.5px;
            margin-top: 0.5rem;
            animation: pulseGlow 2s infinite ease-in-out;
        }}
        @keyframes pulseGlow {{
            0%, 100% {{ transform: scale(1); opacity: 0.85; }}
            50% {{ transform: scale(1.02); opacity: 1; box-shadow: 0 0 15px rgba(245, 158, 11, 0.4); }}
        }}
        .winner-badge-revealed {{
            display: none;
            font-size: 1.35rem;
            font-weight: 900;
            color: #ffffff;
            background: linear-gradient(135deg, #f59e0b, #ec4899, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-top: 0.4rem;
            animation: popBounce 0.45s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
        }}
        .reveal-item.revealed .mystery-box {{ display: none; }}
        .reveal-item.revealed .winner-badge-revealed {{ display: block; }}

        @keyframes popBounce {{
            0% {{ transform: scale(0.6); opacity: 0; }}
            70% {{ transform: scale(1.1); opacity: 1; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}

        /* Headers */
        .kicker {{
            font-size: 0.85rem;
            font-weight: 800;
            letter-spacing: 2px;
            color: var(--accent-cyan);
            text-transform: uppercase;
            margin-bottom: 0.3rem;
        }}
        h1.main-title {{
            font-size: 4rem;
            font-weight: 900;
            line-height: 1.1;
            background: linear-gradient(135deg, #c084fc 0%, #f472b6 50%, #38bdf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 1rem;
        }}
        h2 {{ font-size: 2.3rem; font-weight: 800; margin-bottom: 0.2rem; }}
        .slide-desc {{ color: var(--text-sub); font-size: 1.05rem; margin-bottom: 1.5rem; }}
        .subtitle {{ font-size: 1.5rem; color: var(--text-sub); margin-bottom: 2rem; }}

        /* Cards */
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-glass);
            border-radius: 16px;
            padding: 1.25rem 1.5rem;
            backdrop-filter: blur(16px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }}
        .highlight {{ border-color: rgba(6, 182, 212, 0.35); }}
        .accent {{ border-color: rgba(245, 158, 11, 0.35); }}

        /* Grids */
        .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1.2rem; }}
        .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.2rem; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.2rem; margin-bottom: 1.5rem; }}

        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-glass);
            border-radius: 16px;
            padding: 1.8rem 1.4rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .stat-card .number {{ font-size: 2.8rem; font-weight: 900; color: var(--accent-cyan); line-height: 1; margin-bottom: 0.5rem; }}
        .stat-card .label {{ font-size: 0.9rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #fff; }}
        .stat-card .detail {{ font-size: 0.85rem; color: var(--text-sub); margin-top: 0.4rem; }}

        /* Fun Fact Cards (Replacing Speaker Notes) */
        .fun-fact-box {{
            margin-top: 1.3rem;
            background: linear-gradient(135deg, rgba(139, 92, 246, 0.15) 0%, rgba(6, 182, 212, 0.1) 100%);
            border: 1px solid rgba(139, 92, 246, 0.35);
            padding: 0.85rem 1.3rem;
            border-radius: 12px;
            font-size: 0.98rem;
            color: #e2e8f0;
            box-shadow: 0 4px 20px rgba(139, 92, 246, 0.15);
        }}

        /* Reveal Leaderboards */
        .leaderboard-reveal-grid {{ display: flex; flex-direction: column; gap: 0.8rem; }}
        .tier-box {{ padding: 0.9rem 1.3rem; }}
        .tier-label {{ font-size: 0.8rem; font-weight: 800; color: var(--accent-cyan); letter-spacing: 1.5px; margin-bottom: 0.4rem; display: block; }}
        .tier-row {{ display: flex; justify-content: space-between; font-size: 1.1rem; padding: 0.25rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .tier-row:last-child {{ border-bottom: none; }}
        .mystery-card-container {{ text-align: center; margin-top: 0.5rem; }}
        .winner-reveal-banner {{
            display: none;
            background: linear-gradient(135deg, #8b5cf6, #ec4899);
            border-radius: 16px;
            padding: 1.4rem;
            color: #fff;
            animation: popBounce 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
        }}
        .winner-reveal-banner.gold-bg {{ background: linear-gradient(135deg, #f59e0b, #ec4899); }}
        .reveal-item.revealed .mystery-box {{ display: none; }}
        .reveal-item.revealed .winner-reveal-banner {{ display: block; }}
        .winner-trophy {{ font-size: 0.9rem; font-weight: 800; letter-spacing: 2px; margin-bottom: 0.2rem; }}
        .winner-name-huge {{ font-size: 2.2rem; font-weight: 900; margin-bottom: 0.2rem; }}
        .winner-stat-huge {{ font-size: 1rem; font-weight: 700; opacity: 0.9; }}

        /* Speed Step Cards */
        .speed-reveal-container {{ display: flex; flex-direction: column; gap: 0.75rem; }}
        .speed-step-card {{ display: flex; align-items: center; justify-content: space-between; padding: 0.85rem 1.4rem; }}
        .speed-content-wrap {{ font-size: 1.15rem; }}
        .speed-desc {{ font-size: 0.85rem; color: var(--text-sub); }}

        /* Dialogues & Bubbles */
        .chat-dialogue {{ display: flex; flex-direction: column; gap: 0.9rem; margin: 1rem 0; }}
        .chat-row {{ display: flex; align-items: center; gap: 1rem; }}
        .chat-row.right {{ flex-direction: row-reverse; }}
        .avatar {{
            font-size: 0.85rem;
            font-weight: 800;
            background: rgba(255, 255, 255, 0.1);
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            min-width: 80px;
            text-align: center;
        }}
        .bubble {{
            background: var(--bg-card);
            border: 1px solid var(--border-glass);
            padding: 0.9rem 1.3rem;
            border-radius: 18px;
            font-size: 1.1rem;
            max-width: 75%;
        }}
        .bubble.wide {{ font-size: 0.98rem; }}
        .chat-row.right .bubble {{ background: rgba(139, 92, 246, 0.25); border-color: rgba(139, 92, 246, 0.4); }}
        .rx-tag {{
            display: inline-block;
            background: rgba(236, 72, 153, 0.25);
            color: #f472b6;
            font-weight: 800;
            font-size: 0.75rem;
            padding: 0.15rem 0.45rem;
            border-radius: 6px;
            margin-right: 0.4rem;
        }}

        /* Flow state bubbles */
        .bubble-stack {{ display: flex; flex-direction: column; gap: 0.6rem; margin-top: 1rem; }}
        .chat-bubble {{ background: var(--bg-card); border: 1px solid var(--border-glass); padding: 0.8rem 1.2rem; border-radius: 12px; font-size: 1.1rem; }}
        .rx-pill {{ background: rgba(236, 72, 153, 0.25); color: #f472b6; font-weight: 800; font-size: 0.8rem; padding: 0.2rem 0.5rem; border-radius: 6px; margin-right: 0.5rem; }}

        /* Crickets */
        .cricket-container {{ display: flex; flex-direction: column; gap: 0.9rem; }}
        .cricket-card {{ border-left: 4px solid var(--accent-pink); padding: 0.9rem 1.3rem; }}
        .cricket-header {{ display: flex; justify-content: space-between; margin-bottom: 0.4rem; }}
        .cricket-quote {{ font-size: 1rem; font-style: italic; color: #f1f5f9; margin-bottom: 0.3rem; }}
        .cricket-date {{ font-size: 0.8rem; color: var(--text-sub); }}
        .rx-zero {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}

        /* Snipers & Awards */
        .sniper-card {{ display: flex; flex-direction: column; justify-content: space-between; }}
        .sniper-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem; }}
        .sniper-date {{ font-size: 0.8rem; color: var(--text-sub); }}
        .sniper-text {{ font-size: 1.05rem; font-style: italic; margin-bottom: 0.8rem; }}
        .sniper-footer {{ display: flex; justify-content: space-between; border-top: 1px solid var(--border-glass); padding-top: 0.5rem; font-size: 0.95rem; }}

        .awards-reveal-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }}
        .award-reveal-card {{
            border: 1px solid rgba(245, 158, 11, 0.3);
            background: linear-gradient(180deg, rgba(245, 158, 11, 0.08) 0%, rgba(26, 29, 46, 0.8) 100%);
            padding: 1.1rem 1.3rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        .award-trophy {{ font-size: 0.85rem; font-weight: 800; color: var(--accent-gold); text-transform: uppercase; letter-spacing: 1px; }}
        .award-setup {{ font-size: 0.88rem; color: var(--text-sub); margin: 0.4rem 0; }}

        /* Badges */
        .badge {{ padding: 0.25rem 0.6rem; border-radius: 6px; font-size: 0.8rem; font-weight: 700; }}
        .rx-badge {{ background: rgba(236, 72, 153, 0.25); color: #f472b6; }}
        .gold-badge {{ background: rgba(245, 158, 11, 0.25); color: #f59e0b; }}
        .silver-badge {{ background: rgba(148, 163, 184, 0.25); color: #cbd5e1; }}
        .sloth-badge {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}

        /* Simp card */
        .simp-card {{ background: rgba(255,255,255,0.04); padding: 0.6rem 0.9rem; border-radius: 8px; margin-bottom: 0.4rem; }}
        .simp-pair {{ font-size: 1rem; margin-bottom: 0.2rem; }}
        .simp-detail {{ font-size: 0.82rem; color: var(--text-sub); }}

        /* Lists & Meta */
        .styled-list {{ list-style: none; display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.5rem; }}
        .styled-list li {{ font-size: 0.95rem; border-bottom: 1px solid rgba(255,255,255,0.04); padding-bottom: 0.3rem; }}
        .meta-tag-strip {{ display: flex; gap: 0.8rem; margin-bottom: 2rem; }}
        .tag {{ background: rgba(255, 255, 255, 0.08); padding: 0.4rem 0.9rem; border-radius: 20px; font-size: 0.95rem; font-weight: 600; }}
        .nav-hint {{ font-size: 0.95rem; color: var(--text-sub); margin-top: 1.5rem; }}
        kbd {{ background: rgba(255, 255, 255, 0.15); padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 700; color: #fff; }}

        /* Controls Overlay */
        .deck-controls {{
            position: fixed;
            bottom: 1.5rem;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 0.8rem;
            background: rgba(15, 17, 26, 0.88);
            border: 1px solid var(--border-glass);
            padding: 0.5rem 1.2rem;
            border-radius: 30px;
            backdrop-filter: blur(12px);
            z-index: 100;
        }}
        .ctrl-btn {{
            background: none;
            border: none;
            color: #fff;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            transition: background 0.2s;
        }}
        .ctrl-btn:hover {{ background: rgba(255, 255, 255, 0.1); }}
        .slide-counter {{ font-size: 0.85rem; font-weight: 700; color: var(--text-sub); min-width: 70px; text-align: center; }}
        .step-pill {{
            background: rgba(139, 92, 246, 0.25);
            color: #c4b5fd;
            font-size: 0.75rem;
            font-weight: 800;
            padding: 0.2rem 0.6rem;
            border-radius: 12px;
            letter-spacing: 0.5px;
        }}
        .progress-line {{
            position: fixed;
            top: 0;
            left: 0;
            height: 3px;
            background: linear-gradient(90deg, #8b5cf6, #ec4899, #06b6d4);
            transition: width 0.3s ease;
            z-index: 101;
        }}
        .restart-btn {{
            background: linear-gradient(135deg, #8b5cf6, #ec4899);
            border: none;
            color: #fff;
            padding: 0.8rem 1.8rem;
            font-size: 1.1rem;
            font-weight: 700;
            border-radius: 30px;
            cursor: pointer;
            margin-top: 1.5rem;
        }}
    </style>
    </head>
    <body>

    <canvas id="confettiCanvas"></canvas>
    <div class="progress-line" id="progressBar"></div>

    <div class="deck" id="deckContainer">
    {slides_html}
    </div>

    <div class="deck-controls">
        <button class="ctrl-btn" onclick="prev()" title="Previous (Left Arrow)">◀</button>
        <span class="slide-counter" id="slideCounter">1 / {len(slides)}</span>
        <span class="step-pill" id="stepPill">Step 0/0</span>
        <button class="ctrl-btn" onclick="next()" title="Next / Reveal (Right Arrow / Space)">▶</button>
        <button class="ctrl-btn" onclick="toggleFullScreen()" title="Toggle Fullscreen (F)">⛶</button>
    </div>

    <script>
        const slides = document.querySelectorAll('.slide');
        const totalSlides = slides.length;
        let currentSlide = 0;
        let currentStep = 0;

        // Confetti Engine
        const canvas = document.getElementById('confettiCanvas');
        const ctx = canvas.getContext('2d');
        let particles = [];

        function resizeCanvas() {{
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }}
        window.addEventListener('resize', resizeCanvas);
        resizeCanvas();

        function launchConfetti(x = canvas.width / 2, y = canvas.height / 2, count = 60) {{
            const colors = ['#8b5cf6', '#ec4899', '#06b6d4', '#f59e0b', '#10b981', '#ffffff'];
            for (let i = 0; i < count; i++) {{
                particles.push({{
                    x: x,
                    y: y,
                    vx: (Math.random() - 0.5) * 16,
                    vy: (Math.random() - 0.7) * 18,
                    size: Math.random() * 8 + 4,
                    color: colors[Math.floor(Math.random() * colors.length)],
                    alpha: 1,
                    decay: Math.random() * 0.015 + 0.01
                }});
            }}
        }}

        function animateConfetti() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            for (let i = particles.length - 1; i >= 0; i--) {{
                const p = particles[i];
                p.x += p.vx;
                p.y += p.vy;
                p.vy += 0.45; // gravity
                p.alpha -= p.decay;
                if (p.alpha <= 0) {{
                    particles.splice(i, 1);
                }} else {{
                    ctx.save();
                    ctx.globalAlpha = p.alpha;
                    ctx.fillStyle = p.color;
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.size / 2, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.restore();
                }}
            }}
            requestAnimationFrame(animateConfetti);
        }}
        animateConfetti();

        // Step & Slide Engine
        function getSlideItems(slideIndex) {{
            return slides[slideIndex].querySelectorAll('.reveal-item');
        }}

        function updateDock() {{
            const items = getSlideItems(currentSlide);
            const totalSteps = items.length;
            document.getElementById('slideCounter').innerText = `${{currentSlide + 1}} / ${{totalSlides}}`;
            document.getElementById('stepPill').innerText = totalSteps > 0 ? `Reveal ${{currentStep}}/${{totalSteps}}` : 'Ready';
            const progressPct = ((currentSlide + (totalSteps ? currentStep / (totalSteps + 1) : 0)) / totalSlides) * 100;
            document.getElementById('progressBar').style.width = `${{progressPct}}%`;
        }}

        function goToSlide(index, revealAll = false) {{
            if (index < 0) index = 0;
            if (index >= totalSlides) index = totalSlides - 1;
            currentSlide = index;

            slides.forEach((s, idx) => {{
                s.classList.toggle('active', idx === currentSlide);
            }});

            const items = getSlideItems(currentSlide);
            if (revealAll) {{
                currentStep = items.length;
                items.forEach(it => it.classList.add('revealed'));
            }} else {{
                currentStep = 0;
                items.forEach(it => it.classList.remove('revealed'));
            }}

            // Trigger confetti on finale
            if (currentSlide === totalSlides - 1) {{
                launchConfetti(canvas.width / 2, canvas.height / 3, 120);
            }}

            updateDock();
        }}

        function next() {{
            const items = getSlideItems(currentSlide);
            if (currentStep < items.length) {{
                const item = items[currentStep];
                item.classList.add('revealed');
                currentStep++;

                // If item has a winner or mystery box, launch celebratory confetti
                if (item.classList.contains('award-reveal-card') || item.classList.contains('mystery-card-container')) {{
                    launchConfetti(window.innerWidth / 2, window.innerHeight / 2, 70);
                }}

                updateDock();
            }} else {{
                if (currentSlide < totalSlides - 1) {{
                    goToSlide(currentSlide + 1, false);
                }}
            }}
        }}

        function prev() {{
            const items = getSlideItems(currentSlide);
            if (currentStep > 0) {{
                currentStep--;
                items[currentStep].classList.remove('revealed');
                updateDock();
            }} else {{
                if (currentSlide > 0) {{
                    goToSlide(currentSlide - 1, true);
                }}
            }}
        }}

        function toggleFullScreen() {{
            if (!document.fullscreenElement) {{
                document.documentElement.requestFullscreen().catch(() => {{}});
            }} else {{
                if (document.exitFullscreen) document.exitFullscreen();
            }}
        }}

        // Keyboard handlers
        window.addEventListener('keydown', (e) => {{
            if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown' || e.key === 'Enter') {{
                e.preventDefault();
                next();
            }} else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || e.key === 'Backspace') {{
                e.preventDefault();
                prev();
            }} else if (e.key === 'f' || e.key === 'F') {{
                toggleFullScreen();
            }} else if (e.key === 'Home') {{
                goToSlide(0);
            }} else if (e.key === 'End') {{
                goToSlide(totalSlides - 1, true);
            }}
        }});

        // Click on slide background advances reveal/slide
        document.getElementById('deckContainer').addEventListener('click', (e) => {{
            if (!e.target.closest('.deck-controls') && !e.target.closest('.restart-btn')) {{
                next();
            }}
        }});

        // Initialize
        goToSlide(0);
    </script>
    </body>
    </html>
    """



    with open(output_path, "w", encoding="utf-8") as f_out:
        f_out.write(full_html)
    print(f"[+] Dramatic Keynote Presentation (Version 2) generated at '{output_path}'!")


if __name__ == "__main__":
    data = load_chat_data()
    if data:
        analyze_chat(data[0], data[1])
