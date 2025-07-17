import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import math

class SimaiToDynamixConverter:
    """
    将舞萌 (simai) 谱面格式（包括Tap, Hold, Slide）转换为 Dynamix XML 谱面格式。
    """
    def __init__(self,
                 position_max=4.9,
                 position_min=-0.7,
                 default_width=0.8,
                 break_multiplier=3,
                 difficulty='G',
                 slide_chain_interval_beats=0.125):
        self.position_max = position_max
        self.position_min = position_min
        self.default_width = default_width
        self.break_multiplier = break_multiplier
        self.difficulty = difficulty
        self.slide_chain_interval_beats = slide_chain_interval_beats
        self._reset_state()

    def _reset_state(self):
        self.title = "Untitled"
        self.artist = "Unknown"
        self.first_bpm = None
        self.bpm_events = []
        self.note_events = []
        self.current_time = 0.0
        self.note_id_counter = 0

    def _calculate_position(self, key_number):
        return self.position_min + ((8.0 - key_number) / 7.0) * (self.position_max - self.position_min)

    def _parse_tap_hold_string(self, note_str):
        match = re.match(r'(\d+)(b)?(?:h\[(\d+):([\d.]+)\])?', note_str)
        if not match: return None
        note_info = {
            "key": int(match.group(1)), "is_break": bool(match.group(2)),
            "type": "HOLD" if match.group(3) else "TAP", "duration_beats": 0
        }
        if note_info["type"] == "HOLD":
            note_info["duration_beats"] = float(match.group(4)) * (4.0 / int(match.group(3)))
        return note_info

    def _parse_slide_string(self, slide_str):
        start_key_match = re.match(r'(\d+)', slide_str)
        if not start_key_match: return None
        start_key = int(start_key_match.group(1))
        segments_str = slide_str[start_key_match.end():]
        segment_pattern = r'([-><^vpqszwV]+)(\d+)(b)?\[(\d+):([\d.]+)\]'
        matches = re.findall(segment_pattern, segments_str)
        if not matches: return None
        segments = []
        current_key = start_key
        for path_type, end_key_str, is_break_str, div, count in matches:
            segments.append({
                "start_key": current_key, "end_key": int(end_key_str),
                "path_type": path_type, "is_break": bool(is_break_str),
                "duration_beats": float(count) * (4.0 / int(div))
            })
            current_key = int(end_key_str)
        return segments

    def _add_note_event(self, note_info, start_time):
        multiplier = self.break_multiplier if note_info["is_break"] else 1
        position = self._calculate_position(note_info["key"])
        for _ in range(multiplier):
            if note_info["type"] == "TAP":
                self.note_events.append({
                    'm_id': self.note_id_counter, 'm_type': 'NORMAL', 'm_time': start_time,
                    'm_position': position, 'm_width': self.default_width, 'm_subId': -1
                })
                self.note_id_counter += 1
            elif note_info["type"] == "HOLD":
                duration_measures = note_info["duration_beats"] / 4.0
                end_time = start_time + duration_measures
                hold_id, sub_id = self.note_id_counter, self.note_id_counter + 1
                self.note_events.append({
                    'm_id': hold_id, 'm_type': 'HOLD', 'm_time': start_time,
                    'm_position': position, 'm_width': self.default_width, 'm_subId': sub_id
                })
                self.note_events.append({
                    'm_id': sub_id, 'm_type': 'SUB', 'm_time': end_time,
                    'm_position': position, 'm_width': self.default_width, 'm_subId': -1
                })
                self.note_id_counter += 2

    def _add_slide_event(self, segments, start_time):
        is_break_slide = any(seg['is_break'] for seg in segments)
        multiplier = self.break_multiplier if is_break_slide else 1
        for _ in range(multiplier):
            segment_start_time = start_time
            start_pos = self._calculate_position(segments[0]['start_key'])
            self.note_events.append({
                'm_id': self.note_id_counter, 'm_type': 'NORMAL', 'm_time': segment_start_time,
                'm_position': start_pos, 'm_width': self.default_width, 'm_subId': -1
            })
            self.note_id_counter += 1
            for seg in segments:
                num_chains = math.floor(seg['duration_beats'] / self.slide_chain_interval_beats)
                if num_chains <= 0:
                    segment_start_time += seg['duration_beats'] / 4.0
                    continue
                start_k, end_k = float(seg['start_key']), float(seg['end_key'])
                path_logic, direction = 'straight', 0
                if seg['path_type'] == '<':
                    path_logic, direction = 'circular', -1.0 if start_k in [1, 2, 7, 8] else 1.0
                elif seg['path_type'] == '>':
                    path_logic, direction = 'circular', 1.0 if start_k in [1, 2, 7, 8] else -1.0
                for i in range(1, int(num_chains) + 1):
                    progress = float(i) / num_chains
                    chain_time = segment_start_time + progress * (seg['duration_beats'] / 4.0)
                    if path_logic == 'straight':
                        current_key = start_k + (end_k - start_k) * progress
                    else:
                        dist = (end_k - start_k) if end_k >= start_k else (8.0 - start_k + end_k) if direction == 1.0 else (start_k - end_k) if start_k >= end_k else (start_k + 8.0 - end_k)
                        current_key_unwrapped = start_k + direction * dist * progress
                        current_key = (current_key_unwrapped - 1.0) % 8.0 + 1.0
                    chain_pos = self._calculate_position(current_key)
                    if chain_pos < -1.1:
                        chain_pos += 6.4
                    self.note_events.append({
                        'm_id': self.note_id_counter, 'm_type': 'CHAIN', 'm_time': chain_time,
                        'm_position': chain_pos, 'm_width': self.default_width, 'm_subId': -1
                    })
                    self.note_id_counter += 1
                segment_start_time += seg['duration_beats'] / 4.0

    def convert_and_save(self, simai_content):
        xml_output = self._convert(simai_content)
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", self.title)
        filename = f"_dym_{safe_title}_{self.difficulty}-{timestamp}.xml"
        try:
            with open(filename, "w", encoding="utf-8") as f: f.write(xml_output)
            print(f"转换成功！文件已保存为: {filename}")
        except Exception as e:
            print(f"保存文件时出错: {e}")

    def _convert(self, simai_content):
        self._reset_state()
        lines = simai_content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or line == 'E': continue
            if line.startswith('&'):
                if line.startswith('&title='): self.title = line.split('=', 1)[1]
                elif line.startswith('&artist='): self.artist = line.split('=', 1)[1]
                continue
            
            # --- BPM Logic ---
            original_line = line
            bpm_matches = list(re.finditer(r'\((\d+\.?\d*)\)', line))
            if bpm_matches:
                for match in bpm_matches:
                    bpm_val = float(match.group(1))
                    time_offset = 0.0
                    prefix = original_line[:match.start()]
                    prefix_content = re.sub(r'\{(\d+)\}', '', prefix).strip()
                    if prefix_content and ',' in prefix_content:
                        division_match_for_bpm = re.search(r'\{(\d+)\}', original_line)
                        if division_match_for_bpm:
                            notes_part_clean = re.sub(r'\(\d+\.?\d*\)', '', original_line[division_match_for_bpm.end():])
                            note_slots_for_bpm = [s for s in notes_part_clean.split(',') if s]
                            total_slots = len(note_slots_for_bpm)
                            slots_before = prefix[division_match_for_bpm.end():].count(',')
                            if total_slots > 0:
                                time_offset = float(slots_before) / total_slots
                    bpm_time = self.current_time + time_offset
                    dynamix_bpm = bpm_val / 4.0
                    self.bpm_events.append({'m_time': bpm_time, 'm_value': dynamix_bpm})
                    if self.first_bpm is None: self.first_bpm = dynamix_bpm
                line = re.sub(r'\(\d+\.?\d*\)', '', line)

            # --- Note Parsing Logic ---
            division_match = re.search(r'\{(\d+)\}', line)
            if not division_match:
                self.current_time += 1.0; continue
            note_content = line[division_match.end():]
            note_slots = note_content.split(',')
            if note_slots and not note_slots[-1]: note_slots.pop()
            if not note_slots:
                self.current_time += 1.0; continue
            num_slots = len(note_slots)
            for i, slot in enumerate(note_slots):
                slot = slot.strip()
                if not slot: continue
                slot_start_time = self.current_time + (i / num_slots)
                concurrent_notes = slot.split('/')
                for note_str in concurrent_notes:
                    note_str = note_str.strip()
                    if not note_str: continue
                    if '*' in note_str or re.search(r'[-><^vpqszwV]', note_str):
                        slide_groups = note_str.split('*')
                        first_group, start_key_str = slide_groups[0], re.match(r'(\d+)', slide_groups[0]).group(1)
                        segments = self._parse_slide_string(first_group)
                        if segments: self._add_slide_event(segments, slot_start_time)
                        for other_group in slide_groups[1:]:
                            segments = self._parse_slide_string(start_key_str + other_group)
                            if segments: self._add_slide_event(segments, slot_start_time)
                    elif 'h[' in note_str:
                        note_info = self._parse_tap_hold_string(note_str)
                        if note_info: self._add_note_event(note_info, slot_start_time)
                    else:
                        note_info = self._parse_tap_hold_string(note_str)
                        if note_info: self._add_note_event(note_info, slot_start_time)
            self.current_time += 1.0
        return self._generate_xml()

    def _generate_xml(self):
        root = ET.Element("CMap", {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xmlns:xsd": "http://www.w3.org/2001/XMLSchema"})
        ET.SubElement(root, "m_path").text = self.title
        ET.SubElement(root, "m_mapID").text = f"_dym_{self.title}_{self.difficulty}"
        ET.SubElement(root, "m_barPerMin").text = f"{self.first_bpm or 30.0:.6f}"
        ET.SubElement(root, "m_timeOffset").text = "0.000000"
        ET.SubElement(root, "m_leftRegion").text = "PAD"
        ET.SubElement(root, "m_rightRegion").text = "PAD"
        argument = ET.SubElement(root, "m_argument")
        bpmchange_list = ET.SubElement(argument, "m_bpmchange")
        if not self.bpm_events: self.bpm_events.append({'m_time': 0.0, 'm_value': self.first_bpm or 30.0})
        for bpm in sorted(self.bpm_events, key=lambda x: x['m_time']):
            cbpmchange = ET.SubElement(bpmchange_list, "CBpmchange")
            ET.SubElement(cbpmchange, "m_value").text = f"{bpm['m_value']:.6f}"
            ET.SubElement(cbpmchange, "m_time").text = f"{bpm['m_time']:.6f}"
        notes_container_main = ET.SubElement(root, "m_notes"); notes_list_main = ET.SubElement(notes_container_main, "m_notes")
        notes_container_left = ET.SubElement(root, "m_notesLeft"); ET.SubElement(notes_container_left, "m_notes")
        notes_container_right = ET.SubElement(root, "m_notesRight"); ET.SubElement(notes_container_right, "m_notes")
        for note in sorted(self.note_events, key=lambda x: (x['m_time'], x['m_id'])):
            note_asset = ET.SubElement(notes_list_main, "CMapNoteAsset")
            ET.SubElement(note_asset, "m_id").text = str(note['m_id'])
            ET.SubElement(note_asset, "m_type").text = note['m_type']
            ET.SubElement(note_asset, "m_time").text = f"{note['m_time']:.6f}"
            ET.SubElement(note_asset, "m_position").text = f"{note['m_position']:.4f}"
            ET.SubElement(note_asset, "m_width").text = f"{note['m_width']:.2f}"
            ET.SubElement(note_asset, "m_subId").text = str(note['m_subId'])
        rough_string = ET.tostring(root, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="\t", encoding="utf-8").decode('utf-8')
        return "\n".join([line for line in pretty_xml.split("\n") if line.strip()])

# --- 主程序入口 ---（注意将 &inote_6= 和第一行谱面分开，否则将会报错）
if __name__ == '__main__':
    simai_input_data = """
&title=Xaleid◆scopiX
&artist=xi
&first=0
&des_5=TOP SECRET LEAKERS FUCK U
&lv_6=15.0
&inote_6=
(120){1},
{1},
{64}2b,1/3,4/8,5h[64:109]/7,6<4[16:27]>6[4:5],,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
{1},
{1}5h[4:3],
{4}4h[2:1],,5h[4:1],6,
{1}6h[4:7]/7b>5[4:5],
{4},,,5-8[8:1],
{4}5h[4:3],,,4-1b[8:1],
{2}4h[4:3],8h[4:1],
{16}5h[8:1]/6h[8:1],,,5h[8:1]/6h[8:1],,,6h[8:1]/7h[8:1],,,7h[8:1]/8-4[16:1],,,1/8,,,,
(130){16}3h[8:1]/4h[8:1],,,3h[8:1]/4h[8:1],,,2h[8:1]/3h[8:1],,,2h[8:1]/1<4[16:3],,,,,,,
(140){16}3h[8:1],,,3h[8:1]/4h[8:1],,,2h[8:1]/3h[8:1],,,2h[8:1]/1-5[8:1],,,1/8,,,,
(150){32}6h[8:1],,,,,,6h[8:1]/7h[8:1],,,,,,7h[8:1]/8h[8:1],,,,,,1h[8:1]/8h[8:1],,,,,,A2,E3,E4,A4,A8,E8,E7,A6,
(160){16}1b,,,3,4,,4,,2h[16:3]/5,,,4,3,,2/5,,
(170){16}1/6h[16:3],,,5,7,,7,,6h[8:1]/8h[8:1],,,5h[8:1]/7h[8:1],,,6,5,
(180){16}6bh[16:3],,,5,6,,6,,4/7h[16:3],,,5,6,,4/7,,
(190){16}3h[16:3]/8,,,4,2,,2,,1h[8:1]/2h[8:1],,,3h[8:1]/8h[8:1],,,4/7,,
(200){16}6b/3-1[8:1],,,4,3,,5,,3-7[8:1],,,4,3,,5,,
(210){16}1b/6-8[8:1],,,5,6,,4,,6-2[8:1],,,5,6,,4,,
(220){8}1b/3b,1/3,2/8,2/8,1/7,1/7,6/8,6/8,
(230){16}7,8,7,8,7-1[16:1]-3[16:1]-5[8:1]-7[8:1]*<7[8:3],8,7,8,(240),,,,,,,,
{1}C1f,
{16}3/4,,4/5,,5/6,,4,5,4,5,4,5,3/6-8[8:1],,,,
{8}A6f/1b,,2,2h[4:1],,8,8h[4:1],,
{8}6,6h[4:1],,5,5,,4/6,,
{8}3h[4:1],,5,5h[4:1],,7,7h[4:1],,
{8}1,1h[4:1],,2,2,,1/2,,
{8}3b/8h[4:1],,6,6h[4:1],,5,5h[4:1],,
{8}3,3h[4:1],,1,1,,2/8,,
{2}6h[4:1]/7h[4:1],2h[4:1]/3h[4:1],
{24}4/5,,,,,,1/8,,,,,,5/6,4,3,2,1,8,7,6,5,4,3,2,
{8}1b/5b,,7-4[8:1],7,2-5[8:1],2,8-4[8:1],8,
{16}1-5[8:1],,1,,7-3[8:1],,7,,1>4[8:1],,1,,5,6,7,8,
{8}1bh[4:1],,2-5[8:1],2,7-4[8:1],7,8-3[8:1],8,
{16}6>3[8:1],,6,,8,,8,1,2h[8:1],,8,7,6h[8:1],,4,3,
{16}2bh[4:1],,,,7-3[8:1],,7,,1>4[8:1],,1,,5,6,7,8,
{24}1b-5[8:1],,,1,,,3h[4:1],,,,,,7,6,5,4,3,2,1,8,7,6,5,4,
{3}2bh[4:1]/3bh[4:1],5bh[4:1]/6bh[4:1],2bh[4:1]/4b,
{12}5b,3,4,3,6b,4,5,4,7b,5,6,5,
(180){16}8b,,,,2/6,,3,4,,6,5,,4/7,,3/8,,
{16}2,1,3,,4/8,,7,5,,5,7h[8:1],,5,6,4,5,
{16}3,4,2,,1/5,,7,8,,2,1,,3/8-4[8:1],,7/4-1[8:1]-5[8:1]-7[8:1],,
{4},,8w4[8:1],8,
{16}2b/6b,,,,3,5,4,6,,6,,2,8,,3/7,,
{16}6,4,5,,5,4,6,3,,3,,8,1>4[8:1],,7h[8:1]/8,,
{16}1,,,,6,5,7,4,,4,,1,2,,3/8,,
{16}7bh[8:1],,,6bh[8:1]/8bh[8:1],,,1b/5b,,2,4,3,5,4,6,5,7,
{16}1b,,,,2/5,,3,4,3,,3,4,2-4[8:1]-1[8:1]-5[8:1],,1,,
{48}5,,6,,7,,8,,,,,,8,,,,,,8,,,,,,7,,,,,,7,,,5,,,6,,,,,,6,,,5,,,
{48}7,,,,,,7,,,5,,,6,,,,,,6,,,,,,E3/6h[8:1],,B2,,B1,,E1,,,,,,5/6,,,,,,4/7,,,,,,
{16}3/8,,2,1,4,,6,5,8-3[8:1]*-5[8:1],,,,8,,,,
{16}2b/6b,,,,2/5,,3,4,3,,3,2,3,,3,4,
{16}2,,2,1,2,,2,3,2,,2,1,3,,4/8,,
{16}5/7,,7,6,7,,7,8,7,,7,5,7,,4/6,,
{48}3bh[8:1]/7bh[8:1],,,,,,,,,5bh[8:1]/6bh[8:1],,,,,,,,,3b/4b,,,,,,C1f/1b,,,,,,,,,,,,2/8,,,,3/7,,,,4/6,,,,
{16}5-3[8:1]*<8[8:1],,,,5b,,,,2,1,2,1,3,1,2,1,
{24}2/8,,,2/8,3,4,1,8,7,1,2,3,8,7,6,8,1,2,3/4-8[8:1],,,,,,
{16}4b-6[8:1]*>1[8:1],,,,4,,,,7,8,7,8,6,8,7,8,
{24}1/7,,,1/7,2,3,8,7,6,2,3,4,6/5w1[8:1],,,,,,5,,,,,,
{16}3b/7b,,3,,3,5,3,5,3,4,3,4,3,3,3,3,
{16}3b,,2,1,3-5[8:1]>2[8:1],,4/8,,6,,7,,8h[4:1],,,,
{32}1b,,,,1/8,,,,1/8,,,,1/8,2,3,4,7b,,6,,7,,6,,7,,7,,7,,7,,
{16}7bh[8:1],,8,6h[8:1],,1,5h[8:1],,2b,4,3,5,3,4,2,5,
{1}1bh[4:7],
{4},,,6,
{1}7h[1:2],
{4},,,4,
{1}3h[1:2],
{4}5,,,4,
{1}6h[1:1],
{1}1-4[4:1]-1[4:1]*<8[8:1]-5[8:1]-8[4:1],
{8}1,1,2/8h[4:7],,,4,,,
{8}6,,,4,,,3,,
{8}1b,1,2h[2:3]/8,,,4,,,
{8}5,,,3,,,4<3[2:5],,
{1}3h[4:3],
{1}4h[4:3],
{8}4,,4,,5,5,6,6,
{32}6,,5,,6,,5,,7,,4,,8,,3,,1,,2,,1,,2,,8,7,6,5,4,3,2,1,
{16}4b/8bh[4:1],,,,1,,2/8,,3/7<4[8:1],,4-8[8:1]-6[8:1],,3,2,1,,
{16}1,2,3-5[8:1],,2/5-1[8:1]<7[8:1],,6,,7-3[8:1],,6,,5,,4<8[24:7],,
{16},,1,,1,2,3,4,1,2,3,4,1/5-1[8:1]-3[8:1]-6[8:1],,3/7,,
{16}7,,8,,8,,7h[4:1],,,,5,6,4,5,3,4,
{16}1bh[4:1]/5b,,,,8,,1/7,,6/2>5[8:1],,5-1[8:1]-3[8:1],,6,7,8,,
{16}8,7,6-4[8:1],,7/4-8[8:1]>2[8:1],,3,,2-6[8:1],,3,,4,,5>1[24:7],,
{16},,8,,8,7,6,5,8,7,6,5,4/8,,,,
{16}2b/6b,,3,2b/4b,,6,5b/7b,,8b/4b-8b[8:1],,,,4b/7bh[4:1],,,,
{96}1b,,,2/6,,,3/5,,,4,,,,,,,,,,,,,,,3b/8b,,,,,,,,,,,,,,,,,,,,,,,,2,,,,1,,,,2,,,,1,,,,2,,,,1,,,,2-6b[8:1],,,,,,,,,,,,8,,,,,,,,,,,,
{16}2b/6-2[8:1],,,,1-5[8:1],8,7,6,5-2[8:1],6,7,8,1-6b[8:1],,,,
{8}1/8b-3[8:1]*<5[8:1],,8-4[8:1],,8/4-8[8:1],3,2-6[8:1]>3[8:1]-8[8:1],1,
{8}2,,8,7h[4:1],,8,1/6,,
{16}2b,2,4,3,7,7,5,6,4,5,3,4,1h[4:1],,,,
{16}7b,7,5,6,2,2,4,3,7,7,1,1,2/8h[4:1],,,,
{32}1b,2,3,4,7,6,5,4,1,,1,,7,,7,,2b,3,4,5,8,7,6,5,1,,1,,7,,7,,
{48}2b,,,4,,,3,,,6b,,,4,,,5,,,3b,,,3,,,7b,,6,,5,,4,,3,,2,,1/4b,,5,,6,,7,,8,,1,,
{384}2-7b[8:1]/6-3b[8:1],,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,E1/E5/2b/6b,C1f,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,1/5,,,,,,,,,,,,,,,,,,,,,,,,2/6,,,,,,,,,,,,,,,,,,,,,,,,3/7,,,,,,,,,,,,,,,,,,,,,,,,4/8,,,,,,,,,,,,,,,,,,,,,,,,
{16}1<6[8:1]/5>2[8:1],,,,1/5,,,,1/5,,,3/4,,,5/6,,
{1}B2/A2/B3/E3f/A3/D3/8b,
{1},
(187.5){2}4,4,
{4}4,,4,5,
(195){2}4h[8:3]/6,4h[8:3]/6,
{4}4h[8:3]/6,,4/6,3/5,
(202.5){4}2/4h[8:3],2,2/4h[8:3],2,
{4}2/4h[8:3],2,2/4,2/3,
(210){2}1h[4:1]/2h[4:1],7h[4:1]/8h[4:1],
{4}2/3,6/7,4-1b[8:1]/5-8b[8:1],4/5,
(217.5){8}2b/7b,,3/4,4/5,5/6,6/7,7/8,1/8,
{32}1b/2b,,,,8,7,6,5,3b,,,,8,7,6,5,2b,,,,8,7,6,5,1b,2,3,4,5,6,7,8,
(225){8}1-3[8:1],1-7[8:1],1-4[8:1],1-6[8:1],1-5[8:1],1,1,7,
{16}4/8,,4/8,,5/7,,4,6,1h[8:1],,5,3,8h[8:1],,2,7,
(232.5){48}3,,,3,,,5,,,5,,,4,,,4,,,6,,,6,,,2,,,2,,,7,,,7,,,1,,8,,1,,8,,1,,8,,
{24}2,7,2,7,2,7,3,6,3,6,3,6,4,5,4,5,4,5,3b/6b,,,,,,
(240){24}2/7b,8,1,2,3,4,5,6,7,3b/8,2,1,8,7,6,5,4,3,2/6b,7,8,1,2,3,
{96}4,,,,5,,,,6,,,,2b/7,,,,1,,,,8,,,,7,,,,6,,,,5,,,,4b,,,,3,,,,2,,,,1b,,,8,,,7,,,6,,,5b,,,4,,,3,,,2,,,1b,,,2/8,,,3/7,,,4/6,,,5b,,,4/6,,,3/7,,,2/8,,,
(180){384}1w5[8:1],,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,B1/E1/B2/E2/B3/E3/B4/E4/B5/E5/B6/E6/B7/E7/B8/E8/1b<1[1:2]*>1[1:2],C1f,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
(170){1},
(160){1},
(140){1}C1h[4:3]/B2/B3/E3/B6/B7/E7,
(180){1},
(180){32}C1f/B2h[4:1]/B3h[4:1]/E3h[4:1]/B6h[4:1]/B7h[4:1]/E7h[4:1],,,,,,,,,,,,,,,,D2,D1,D8,D7,D6,D5,D4,D3,D2,,,,,,,,
{32}3/5,,,,5,,2,,5,,,,1/5,,,,6/8,,,,6/8,1,2,3,4/6,,,,6,,,,
{32}4b/7,,,,7,,4,,7,,,,4/7,,,,4/8,3,2,1,8,7,6,5,4/8,,,,,,,,
{16}1,,1,3,1,,1,2,1<6b[8:1]*>4b[8:1],,,,1,,,,
{32}C1f/B2h[8:3]/B3h[8:3]/E3h[8:3]/B6h[16:3]/B7h[16:3]/E7h[16:3],,,,,,,,,,,,,,,,D8,D1,D2,D3,D4,D5,D6,D7,D8,,,,,,,,
{32}4/5b,,,,4,,6,,4,,,,4/7,,,,3/8,,,,1/3,8,7,6,3/5,,,,3,,,,
{32}2/5b,,,,2,,5,,2,,,,2/5,,,,1/5b,6,7,8,1,2,3,4,5/1-6[8:1],,,,,,,,
{48}6/1b-5[8:1],,,,,,,,,4,,,1b>5[16:3],,,,,,7,,,,,,1b,,,,,,,,,,,,E1f/B8,,,,B4/E5f,,,,B6/E7f,,,,
{48}B2/E3f,,,,,,1h[8:1],,,,,,,,,1h[8:1],,,,,,,,,2h[8:1]/8h[8:1],,,,,,,,,2h[8:1]/8h[8:1],,,,,,,,,2/8,,3,,4,,
{48}5h[8:3],,,,,,,,,,,,1,,8,,7,,6h[4:1],,,,,,,,,,,,4,,,,,,8,,,1,,,8,,,1,,,
{24}8b<5[8:1],,,,,,4,3,2,1h[4:1],,,,,,5,2/6,3/7,4/8,,,,,,
{32}B6h[4:1]/B7h[4:1]/E7h[4:1]/1,,,,,,,,1,2,3,4,5,,,,B2h[4:1]/B3h[4:1]/E3h[4:1]/8,,,,,,,,8,7,6,5,4,,,,
{16}1/8b-4[8:3],,,4,4,,3,,3,,3,,2,4,2,4,
{32}1bh[4:1],,,,,,8,,8,,,,1,,,,1/2,,,,1/2,8,7,6,3,,4,,3,,4,,
{96}1b,,,,,,,,,,,,,,,,,,,,,,,,2/8,,,,,,,,,,,,2/8,,,3,,,4,,,5,,,6h[6:1],,,,,,,,,,,,,,,,7,,,,6,,,,7,,,,6,,,,7,,,,6,,,,7,,,,6,,,,
{48}7,,6,,7,,6,,7,,6,,7,,6,,7,,6,,7,,6,,8bh[8:1],,,,,,,,,1bh[8:1]/7bh[8:1],,,,,,,,,2b/6b,,,,,,
{48}3b/5b,,,,,,2,,,4,,,3,,,7,,,5,,,6,,,1,,8,,1,,8,,2,,7,,3,,6,,4,,5,,4,,5,,
{16}4,7,5,6,3>6[4:1]<3[4:1],,,,5h[8:3],,,,,,,,
{16},,5,5,4,6,3,3,4h[16:1],2h[16:1],4h[16:1],2h[16:1],8-2[8:1]-7[8:1]-3[8:1]-6[8:1]-4[8:1]-8[8:1],1h[16:1],8h[16:1],1h[16:1],
{2},8w4[8:1],
{1}1b/7b,
{1},
{1},
{1},
{16}4/5,,,,3,5,3,5,3,5,3,5,2,6,2,6,
{16}1,7,1,7,1,7,1,7,8,8,8,8,8,8,8,8,
{32}1,2,3,,8,,8,,8,,8,,8,,8,,7,6,5,,8,,8,,8,,8,,8,,8,,
{32}1,2,3,,8,,8,,7,6,5,,8,,8,,1,2,3,,7,6,5,,1b,,,,,,,,
{16}3b/7b,,6-2[8:1]-4[16:1]<8[16:3],4,8,,3,,1,,1,,1-4[8:1],,7,7,
{16}1-6[8:1],,5,5,1-5[8:1],,4,4,1,,,,3v2[8:1],4,2V46[16:3],4,
{8}3b,7,8,8,6-1[8:1],5/7,6,4,
{8}2-5[8:1],1/3,2/8-4[8:1],6,4,,3/5,,
{16}B1/B2/E2/7bh[8:1],,,A5h[8:1]/A6h[8:1]/D6h[8:1]/7bh[8:1],,,A1/A2/D2/7bh[8:1],,,B5h[8:1]/B6h[8:1]/E6h[8:1]/7bh[8:1],,,C1f/8b,,,,
{16}B7/B8/E8/2bh[8:1],,,A3h[8:1]/A4h[8:1]/D4h[8:1]/2bh[8:1],,,A7/A8/D8/2bh[8:1],,,B3h[8:1]/B4h[8:1]/E4h[8:1]/2bh[8:1],,,C1f/1b,,1b,,
{16}6bh[8:1]/8h[8:1],,,B4h[8:1]/B5h[8:1]/E5h[8:1]/D5h[8:1]/6bh[8:1],,,6bh[8:1]/8h[8:1],,,B1h[8:1]/B2h[8:1]/E2h[8:1]/D2h[8:1]/6bh[8:1],,,7b/8b,,,,
{16}7/8,6,5,4,7<2[16:5],6>1b[16:5],5>8[16:5],4>7b[16:5],A7,A6,A5,A4,A3/7,A2/6,A1/5,A8/4,
{24}A7f/3bw7b[8:1],,,,,,,,,,,,1,5,1,5,1,5,1,,,6,,,
{16}6h[16:3],,,8,6-8b[8:1],,1,2,6/3<6b[8:1],,1,2,3/6,7,8,,
{48}8h[8:1],,,,,,,,,6h[8:1]/7h[8:1],,,,,,,,,5/6,,,,,,8,,4,,8,,4,,8,,4,,7-3b[8:1],,,,,,1,,,,,,
{64}3/7<4b[8:1],,,,,,,,,,,,2,,,,7>4b[32:3],,,,,,,,3,,,,3h[16:1],,,,,,,,,,,,D5,A5,D6,A6,D7,A7,D8,A8,E1/A1/D1f,,,,,,,,,,,,,,,,
{32}2b/8b,,,,,,7,,2,,,,6,,,,2,3,4,5,6,5,4,3,2,,,,8,,8,,
{48}2b,,,,,,,,,7,,,2,,,,,,6,,,6,,,2,,,,,,5,,4,,5,,3b,,,,3b,,,,3b,,,,
{12}1b,1b,1b,7b,7b,7b,5b,5b,5b,3b,3b,3b,
{12}2b/6b,2b/6b,2b/6b,2b/6b,2b/6b,2b/6b,3b-8b[8:1]-2[8:1]/7b-4b[8:1]-6[8:1],3b/7b,3b/7b,,,,
{8}A2/D2/D3/A6f/D6/D7,,7b/1b-4[8:1]-1[8:1],,1-6b[8:1],6,5,4,
{8}3-8[8:1],6-2b[8:1],3<6[8:1],6-3b[8:1],3w7[8:1],,,,
{8}A1/D1/D2/A5f/D5/D6,,2b/8b-5b[8:1]-8[8:1],,8-3[8:1],3,4,5,
{8}6-1[8:1],3-7b[8:1],6>3[8:1],3-6b[8:1],6w2[8:1],,,,
{16}4b/8b,,7,5,7,5,7,5,6,5,4,5,3,5,2,5,
{16}1b,4,8,3,7,2,6,1,5,1,5,,2/6,,,,
{32}2,3,6,5,2,3,6,5,1,2,7,6,1,2,7,6,2,3,8,7,2,3,8,7,3,4,7,6,3,4,7,6,
{32}1,2,7,6,1,2,7,6,2,3,6,5,2,3,7,6,3,4,7,6,3,4,8,7,2,3,7,6,1,2,6,5,
{1}C1f,
{1},
{1},
{1},
{2}C1fh[1:3]/B2h[1:3],B3h[2:5],
{2}B4h[1:2],B5h[2:3],
{2}B6h[1:1],B7h[2:1],
{16},,,,,,,,1,8,1,8,1,8,1,8,
{32}2,3,4,5,6,,,,2/8,,,,2/8,3,4,5,6,,,,2/8,,,,1,2/8,3/7,4/6,5,,,,
{96}1/7,,,6,,,5,,,4,,,3,,,,,,,,,,,,1/7,,,,,,,,,,,,1/7,,,6,,,5,,,4,,,3,,,,,,,,,,,,1/7,,,,,,,,,,,,2b/6b,,,,,,,,2b/6b,,,,,,,,2b/6b,,,,,,,,
{32}1/5,2,3,4,5,,,,4/8,7,6,5,8,7,6,5,4/8,,,,1,,,,1/5,2,3,4,5,,,,
{32}4/8,7,6,5,4,,,,1/5,2,3,4,1,2,3,4,1/5,,,,2/6,,,,3h[8:1]/7h[8:1],,,,,,,,
{32}4,6,4,6,4,6,4,6,3,7,3,7,2,8,2,8,1,1,1,1,1,,,,B1/B2/E2f/A2/D2/E3,,,,,,,,
{32}5,3,5,3,5,3,5,3,6,2,6,2,7,1,7,1,8,8,8,8,8,,,,B7/E7/A7/B8/E8f/D8,,,,,,,,
{32}1,2,3,4,5,6,7,8,1,2,3,4,1,2,3,4,8,7,6,5,4,3,2,1,8,7,6,5,8,7,6,5,
{32}2,3,4,5,2,3,4,5,6pp4[4:1]*pp4[4:1],7,8,1,2,3,4,5,,,,,,,,,,,,,,,,,
{32}5/7h[1:0],8,1,2,3,4,5/7h[1:0],8,1,2,3,4,5/8h[1:0],7,6,5,4,3,2/8h[1:0],7,6,5,4,3,2/8h[1:0],7,6,5,4,3,2,1,
{32}2h[1:0],3/8,4/7,5,6,7,8,1/6,2/5,3,4,5,6,4/7,3/8,1,2,3,4,2/5,1/6,7,8,1,2,3,2,3,2,3,2,3,
{32}2/4,3/5,4/6,5/7,6,5/7,6/8,1/7,2bh[4:15]/8bh[4:15],,,,,,,,,,,,,,,,,,,,,,,,
{1},
{1},
{1},
{1},
{1},
{1},
E
"""
    print("--- Simai Input ---")
    print(simai_input_data)
    print("\nStarting conversion...")
    converter = SimaiToDynamixConverter()
    converter.convert_and_save(simai_input_data)