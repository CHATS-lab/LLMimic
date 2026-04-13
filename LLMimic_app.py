import os
import json
import streamlit as st
import uuid
import time
import random
import base64
import pandas as pd
from pathlib import Path
from content.utils import Tutor, chat_dialog, render_welcome_screen, render_progress_panel, load_data, stream_write
from pathlib import Path

ss = st.session_state

def build_question_sequence():
    if 'question_sequence' in ss:
        return ss.question_sequence
    
    sequence = []
    
    for phase_idx, (phase_name, phase_path) in enumerate(PHASES):
        # phase description
        sequence.append({
            'type': 'description',
            'phase_idx': phase_idx,
            'phase_name': phase_name,
            'phase_path': phase_path
        })
        
        # Load phase questions
        with open(phase_path) as f:
            data = json.load(f)
        questions = data["questions"]
        
        for q_idx, question in enumerate(questions):
            # regular question
            sequence.append({
                'type': 'question',
                'phase_idx': phase_idx,
                'phase_name': phase_name,
                'phase_path': phase_path,
                'q_idx': q_idx,
                'question': question,
                'is_mc': False
            })
            
            # feedback if exists
            if question.get("feedback") or question.get("congrats"):
                sequence.append({
                    'type': 'feedback',
                    'phase_idx': phase_idx,
                    'phase_name': phase_name,
                    'phase_path': phase_path,
                    'q_idx': q_idx,
                    'question': question
                })
            
            # attention check if exists
            if "MC" in question and question["MC"] and question.get("MC_options"):
                sequence.append({
                    'type': 'question',
                    'phase_idx': phase_idx,
                    'phase_name': phase_name,
                    'phase_path': phase_path,
                    'q_idx': q_idx,
                    'question': question,
                    'is_mc': True
                })
    
    ss.question_sequence = sequence
    return sequence

def get_current_sequence_item():
    """Get current item in the sequence"""
    sequence = build_question_sequence()
    return sequence[ss.sequence_idx] if ss.sequence_idx < len(sequence) else None

def can_go_back():
    """Check if user can go back"""
    if not ss.get("ail_train_welcome", False):
        return False
    return True

def can_go_forward():
    """Check if user can go forward"""
    current_item = get_current_sequence_item()
    if not current_item:
        return False
    
    if current_item['type'] in ['description', 'feedback']:
        # For feedback, check if it's fully displayed
        if current_item['type'] == 'feedback':
            lesson_content = current_item['question'].get("feedback") or current_item['question'].get("congrats")
            if isinstance(lesson_content, str):
                lesson_content = [lesson_content]
            if isinstance(lesson_content, list):
                return ss.feedback_idx >= len(lesson_content)
        return True
    
    if current_item['type'] == 'question':
        # Check if question is answered correctly
        question = current_item['question']
        if current_item['is_mc']:
            question_id = f"{question['id']}-MC"
            options = question.get("MC_options", [])
        else:
            question_id = question["id"]
            options = question["options"]
        
        if question_id not in ss.ail_traj or len(ss.ail_traj[question_id]) == 0:
            return False
        
        latest_attempt = ss.ail_traj[question_id][-1]
        return any(option["text"] == latest_attempt and option["is_correct"] for option in options)
    
    return False

def go_back():
    """Navigate backwards in the sequence"""
    if ss.sequence_idx > 0:
        ss.sequence_idx -= 1
        _clear_states()
        # Set up feedback if landing on feedback screen  
        current_item = get_current_sequence_item()
        if current_item and current_item['type'] == 'feedback':
            _setup_feedback_complete(current_item['question'])
    else:
        # Go back to welcome page
        ss.ail_train_welcome = False
        _clear_states()

def go_forward():
    """Navigate forwards in the sequence"""
    if can_go_forward():
        ss.sequence_idx += 1
        _clear_states()
        
        # Set up feedback if landing on feedback screen
        current_item = get_current_sequence_item()
        if current_item and current_item['type'] == 'feedback':
            _setup_feedback_complete(current_item['question'])
        
        # Check if we've reached the end
        if ss.sequence_idx >= len(build_question_sequence()):
            ss.ailt_finished = True

def _clear_states():
    """Clear states"""
    ss.lock = False
    ss.showing_feedback = False
    ss.feedback_idx = 0
    ss.feedback_accumulated = []
    
    for k in list(ss.keys()):
        if "_shuffled" in k or "_show_success" in k or "_show_error" in k:
            del ss[k]
    
    if 'question_sequence' in ss:
        del ss['question_sequence']

def _setup_feedback_complete(question):
    """Setup feedback """
    lesson_content = question.get("feedback") or question.get("congrats")
    if isinstance(lesson_content, str):
        lesson_content = [lesson_content]
    
    if isinstance(lesson_content, list):
        question_id = question["id"]
        
        # Check if last attempt was correct
        last_attempt_correct = False
        if question_id in ss.ail_traj and len(ss.ail_traj[question_id]) > 0:
            latest_attempt = ss.ail_traj[question_id][-1]
            last_attempt_correct = any(option["text"] == latest_attempt and option["is_correct"] 
                                     for option in question["options"])
        
        # If feedback was shown before AND last attempt is correct -> show complete
        if question_id in ss.feedback_shown and last_attempt_correct:
            ss.showing_feedback = True
            ss.feedback_idx = len(lesson_content)
            ss.feedback_accumulated = lesson_content.copy()
        else:
            # Otherwise -> show step by step (click to continue)
            ss.showing_feedback = True
            ss.feedback_idx = 0
            ss.feedback_accumulated = []

def initialize_session_state():
    """Initialize all session state variables"""
    defaults = {
        "sequence_idx": 0,
        "ail_traj": {},
        "lock": False,
        "showing_feedback": False,
        "feedback_idx": 0,
        "feedback_accumulated": [],
        "ailt_finished": False,
        "intro_shown": set(),
        "loss_tracking": {},
        "question_completed": set(),
        "dialog_closed": False,
        "show_chat_dialog": False,
        "show_description": False,
        "show_description_on_return": False,
        "feedback_shown": set(),  
    }
    
    for key, value in defaults.items():
        if key not in ss:
            ss[key] = value

def handle_option_selection(option, question, phase_name, is_mc=False):
    """Handle option selection for both regular questions and attention checks"""
    if is_mc:
        question_id = f"{question['id']}-MC"
        options_list = question.get("MC_options", [])
    else:
        question_id = question["id"]
        options_list = question["options"]
        
        # Update loss tracking for regular questions
        try:
            loss_point = ss.loss_tracking[phase_name][-1] + float(option.get("point", 0))
            ss.loss_tracking[phase_name].append(loss_point)
        except Exception:
            ss.loss_tracking[phase_name].append(2)
    
    # Record trajectory
    if question_id not in ss.ail_traj:
        ss.ail_traj[question_id] = []
    ss.ail_traj[question_id].append(option["text"])
    
    if option["is_correct"]:
        if is_mc:
            st.success("Correct! Great job recalling the earlier session!", icon=':material/check:')
            ss.question_completed.add(question_id)
            
            shuffle_key = f"{question_id}_shuffled"
            if shuffle_key in ss:
                del ss[shuffle_key]
            time.sleep(0.8)
            
            go_forward()
            st.rerun()
        else:
            ss[f"{question_id}_show_success"] = option["text"]
            st.rerun()
    else:
        success_flag = f"{question_id}_show_success"
        had_success = success_flag in ss
        if had_success:
            del ss[success_flag]
            ss[f"{question_id}_show_error"] = option["text"]
            st.rerun()
        
        # Show the error message
        if is_mc:
            st.error("Please think more carefully and recall the previous session.")
        else:
            st.error(f"**{question.get('hint', 'Try again!')}**")
        
        # Reset completion status
        if question_id in ss.question_completed:
            ss.question_completed.remove(question_id)
        
        # If this is a regular question with feedback and they got it wrong, remove from feedback_shown
        if not is_mc and (question.get("feedback") or question.get("congrats")):
            if question["id"] in ss.feedback_shown:
                ss.feedback_shown.remove(question["id"])

def render_question_screen(current_item):
    """Render question screen for both regular questions and attention checks"""
    question = current_item['question']
    phase_name = current_item['phase_name']
    is_mc = current_item['is_mc']
    
    if is_mc:
        question_id = f"{question['id']}-MC"
        options = question.get("MC_options", [])
        
        # Attention check rendering
        _, cont, __ = st.columns([1,8,1])
        with cont:
            with st.container(border=True): 
                st.subheader("📝 Pop-Up Quiz")

                st.markdown(f"#### {question['MC']}")
                
                # For AC questions, DON'T shuffle and use original order
                shuffled_options = options
                
                # Render options for AC
                if not ss.lock:
                    for i, option in enumerate(shuffled_options):
                        text = option["text"]
                        if "{field}" in text:
                            text = text.format(
                                field=ss.participant['field'].lower()
                            )
                        
                        key = f"MC_opt_{i}"
                        if st.button(text, key=key):
                            handle_option_selection(option, question, phase_name, is_mc)
                else:
                    # Show disabled options when locked
                    for i, option in enumerate(shuffled_options):
                        text = option["text"]
                        if "{field}" in text:
                            text = text.format(
                                field=ss.participant['field'].lower()
                            )
                        key = f"MC_opt_{i}"
                        st.button(text, key=key, disabled=True)
            

        render_navigation_buttons(show_chat=False)
    else:
        question_id = question["id"]
        options = question["options"]

        correct_answer = ss.get(f"{question_id}_show_success", None)
        wrong_answer = ss.get(f"{question_id}_show_error", None)
        
        if wrong_answer:
            del ss[f"{question_id}_show_error"]
        
        left_col, right_col = st.columns([2, 1])
        
        with left_col:
            st.warning(f"#### :material/integration_instructions: {question['instr']}")
            
            with st.container():
                question_text = question['question'].replace("_____", ":orange[______]")

                st.markdown(f"## **{question_text}**")
                
                if "intro" in question:
                    st.info(f"{question['intro']}", icon=':material/notes:')

                if "demoQ" in question and "demoA" in question:
                    _, demo, _ = st.columns([1, 12, 1])
                    with demo:
                        st.success(f"""##### :material/book: :green-background[_Demonstration Data_] \n
##### __Q__: {question['demoQ']}
##### __A__: __{question['demoA']}__""")
                            
                if "ranking" in question:
                    _, rank, _ = st.columns([1,2,1])
                    with rank: st.success(f"""##### :material/graph_7: _Reward Model_: {" > ".join(question['ranking'])}""")

                option_key = f"{question_id}_shuffled"
                if option_key not in ss:
                    ss[option_key] = random.sample(options, len(options))
                shuffled_options = ss[option_key]
                

                for i, option in enumerate(shuffled_options):
                    text = option["text"]
                    if "{field}" in text:
                        text = text.format(
                            field=ss.participant['field'].lower(),
                        )
                    
                    key = f"opt_{i}"

                    if st.button(text, key=key):
                        handle_option_selection(option, question, phase_name, is_mc)

                    if correct_answer and option["text"] == correct_answer:
                        success_message, continue_button = st.columns([3,1], vertical_alignment="center")
                        with success_message:
                            st.success(question.get("success", "Nice job!"))
                        with continue_button:
                            if st.button("Continue :material/transition_push:", type="primary", key="continue_btn", width= 'stretch'):
                                # Clear the success flag
                                del ss[f"{question_id}_show_success"]
                                
                                ss.question_completed.add(question_id)
                                
                                # Clear shuffled options
                                shuffle_key = f"{question_id}_shuffled"
                                if shuffle_key in ss:
                                    del ss[shuffle_key]
                                
                                # Move forward
                                go_forward()
                                st.rerun()
                    
                    if wrong_answer and option["text"] == wrong_answer:
                        st.error(f"**{question.get('hint', 'Try again!')}**")
            
            st.divider()
            render_navigation_buttons(show_chat=True)
        
        with right_col:
            render_progress_panel(current_item)

def render_navigation_buttons(show_chat=True):
    """Render navigation buttons"""
    if show_chat:
        button1, button2, *_, button3 = st.columns([1, 1, 1, 1])
    else:
        button1, _, button2 = st.columns([1, 1, 1])
    
    with button1:
        if not can_go_back():
            st.button(":material/arrow_back_ios: Previous", width='stretch', disabled=True)
        else:
            if st.button(":material/arrow_back_ios: Previous", width='stretch'):
                go_back()
                st.rerun()
    
    with button2:
        if not can_go_forward():
            st.button("Next :material/arrow_forward_ios:", width='stretch', disabled=True)
        else:
            if st.button("Next :material/arrow_forward_ios:", width='stretch', type='primary'):
                go_forward()
                st.rerun()
    
    # Only show chat button for regular questions
    if show_chat:
        with button3:
            if st.button(":material/support_agent: Chat with tutor", type="primary", width='stretch', key="chat_tutor_btn"):
                if ss.dialog_closed:
                    ss.dialog_closed = False
                ss.show_chat_dialog = True
        
        # Handle chat dialog
        if ss.show_chat_dialog and not ss.dialog_closed:
            chat_dialog()

def render_feedback_screen(current_item):
    """Render the takeaway screen with enhanced diagram support"""
    feedback = st.empty()
    with feedback.container():
        question = current_item['question']
        feedback_list = question.get("feedback", [])
        congrats_list = question.get("congrats", [])
        
        lesson_list = feedback_list if feedback_list else congrats_list
        is_congrats = not feedback_list and congrats_list
        
        if isinstance(lesson_list, str):
            lesson_list = [lesson_list]

        idx = ss.feedback_idx
        if lesson_list:
            if is_congrats:
                st.markdown("### 🎉 Milestone Achieved")
            else:
                st.markdown("### 💬 Takeaway")
        
        # Display accumulated feedback with enhanced formatting
        for line in ss.feedback_accumulated:
            if "{field}" in line:
                formatted_line = line.format(
                    field=ss.participant.get('field', '').lower(),
                )
            else: formatted_line = line
            
            if '[diagram:' in formatted_line:
                diagram_start = formatted_line.find('[diagram:')
                diagram_end = formatted_line.find(']', diagram_start)
                if diagram_end != -1:
                    diagram_file = formatted_line[diagram_start+9:diagram_end] + '.gif'
                    
                    text_before = formatted_line[:diagram_start].strip()
                    if text_before:
                        st.markdown(f"### {text_before}")
                    
                    image_path = Path(f"images/{diagram_file}")
                    if image_path.exists():
                        col1, col2, col3 = st.columns([1, 2, 1])
                        with col2:
                            try:
                                with open(image_path, "rb") as img_file:
                                    img_data = base64.b64encode(img_file.read()).decode()
                                st.markdown(
                                    f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/gif;base64,{img_data}" style="max-width: 100%; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);"></div>',
                                    unsafe_allow_html=True
                                )
                            except Exception as e:
                                st.warning(f"Could not display diagram: {diagram_file}")
                    else:
                        st.warning(f"Diagram not found: {diagram_file}")
                    
                    text_after = formatted_line[diagram_end+1:].strip()
                    if text_after:
                        st.markdown(f"### {text_after}")
                else:
                    st.markdown(f"### {formatted_line}")
            else:
                st.markdown(f"### {formatted_line}")

        if idx < len(lesson_list):
            # showing lesson step by step
            col1, col2, col3 = st.columns([1, 1, 1])
            with col3:
                if st.button("Click to Continue :material/arrow_circle_right:", width='stretch', type="primary"):
                    temp_text = lesson_list[idx]
                    if "{field}" in temp_text:
                        temp_text = temp_text.format(
                            field=ss.participant['field']
                        )
                    ss.feedback_accumulated.append(temp_text)
                    ss.feedback_idx += 1
                    
                    # When feedback is fully shown, add question to feedback_shown set
                    if ss.feedback_idx >= len(lesson_list):
                        question_id = question["id"]
                        ss.feedback_shown.add(question_id)
                    
                    st.rerun()
        else:
            # Lesson completely displayed
            col1, col2, col3 = st.columns([1, 1, 1])
            with col1:
                if not can_go_back():
                    st.button(":material/arrow_back_ios: Previous", width='stretch', disabled=True)
                else:
                    if st.button(":material/arrow_back_ios: Previous", width='stretch'):
                        go_back()
                        st.rerun()
            
            with col3:
                if st.button("🚀 Continue to the Next Level!", width='stretch', type="primary"):
                    feedback.empty()
                    go_forward()
                    st.rerun()

def render_description_content(description, is_first_time=False):
    """Render description content - stream if first time, display instantly if revisited"""
    if is_first_time:
        stream_write(description)
    else:
        for item in description:
            if item.strip():
                if item.strip().startswith('[diagram'):
                    if ':' in item.strip():
                        diagram_file = item.strip().split(':')[1].rstrip(']')
                        diagram_file += '.gif'
                    
                    image_path = Path(f"images/{diagram_file}")
                    if image_path.exists():
                        left_col, img_col, right_col = st.columns([1, 2, 1])
                        with img_col:
                            with open(image_path, "rb") as img_file:
                                img_data = base64.b64encode(img_file.read()).decode()
                            st.markdown(
                                f'<img src="data:image/gif;base64,{img_data}" style="width: 80%; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">',
                                unsafe_allow_html=True
                            )
                    else:
                        st.warning(f"Image not found: {image_path}")
                else:
                    st.markdown(f"### {item}")

def render_description_screen(current_item):
    """Render description screen - simplified version"""
    phase_name = current_item['phase_name']
    phase_path = current_item['phase_path']
    
    names = {
        "PreTrain": "Pre-Training",
        "SFT": "Supervised Fine-Tuning", 
        "RLHF": "Reinforcement Learning from Human Feedback"
    }
    
    # Show participant badge and title
    st.badge(f"Your participant ID is: {ss.participant['id']}", icon= ':material/info:', color="grey")
    st.title(f":material/modeling: {names[phase_name]} Phase")
    
    # Load and render description
    data = load_data(phase_path)
    description = data.get('description', [])
    
    # Check if this is first time seeing this description
    is_first_time = phase_name not in ss.intro_shown
    
    # Render the content
    render_description_content(description, is_first_time)
    
    if is_first_time:
        ss.intro_shown.add(phase_name)
    
    # Navigation buttons
    button1, _, button2 = st.columns([1, 1, 1])
    
    with button1:
        if not can_go_back():
            st.button(":material/arrow_back_ios: Previous", width='stretch', disabled=True)
        else:
            if st.button(":material/arrow_back_ios: Previous", width='stretch'):
                go_back()
                st.rerun()
    
    with button2:
        if st.button("🚀 Let's Start This Phase!", width='stretch', type="primary"):
            go_forward()
            st.rerun()



def record_data():
    """Record user data and trajectory"""
    end = time.time()
    ss.ail_train_duration = round(end - ss.ail_train_start, 2)
    
    save_dict = {
        "participant_id": ss.participant['id'],
        "duration": ss.ail_train_duration,
        "trajectory": ss.ail_traj,
        "messages": ss.tutor.log if hasattr(ss, 'tutor') else [],
    }

    # DB Storage here if you have a database set up, otherwise save locally

    file_path = Path(f"Responses/LLMimicTraj/{ss.participant['id']}.json")
    file_path.parent.mkdir(parents=True, exist_ok=True) 
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(save_dict, f, indent=2)

def main():
    """Main application function"""
    st.set_page_config(
        layout="wide", 
        page_title="LLMimic", 
        page_icon=":material/modeling:", 
        initial_sidebar_state="collapsed"
    )

    st.markdown("""
        <style>
        button[kind="primary"], button[kind="secondary"] {
            text-align: center !important;
        }
        div[data-testid="stButton"] > button {
            text-align: center !important;
            white-space: nowrap !important;
        }
        div[data-testid="stButton"] > button > div {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 4px !important;
        }
        div[data-testid="stButton"] > button p {
            text-align: center !important;
            margin: 0 !important;
            white-space: nowrap !important;
        }
        </style>
        """, unsafe_allow_html=True)
    
    # Initialize participant info
    participant = {
        'id': "LLMimic-Demo"+ str(uuid.uuid4()),
        "country": "USA",
        "field": "Marketing",
        "education": "Master"
    }
    if "participant" not in ss:
        ss.participant = participant
    
    initialize_session_state()
    
    if "ail_train_start" not in ss:
        ss.ail_train_start = time.time()
    
    # Define phases
    base_path = Path.cwd()
    global PHASES
    PHASES = [
        ("PreTrain", base_path / "content" / "PreTrain.json"),
        ("SFT", base_path / "content" / "SFT.json"),
        ("RLHF", base_path / "content" / "RLHF.json")
    ]
    _, page, __ = st.columns([1,10,1])
    with page:
    
        # Show welcome screen if not started
        if not ss.get("ail_train_welcome", False):
            render_welcome_screen()
            st.stop()
        
        # Build the sequence and get current item
        sequence = build_question_sequence()
        current_item = get_current_sequence_item()
        
        # Check if data files exist
        for phase_name, phase_path in PHASES:
            if not phase_path.exists():
                st.error(f"LMT File Not Found")
                st.stop()

        # Initialize loss tracking for all phases
        for phase_name, _ in PHASES:
            if phase_name not in ss.loss_tracking:
                ss.loss_tracking[phase_name] = [0] if phase_name == "RLHF" else [5]

        # Handle completion
        if ss.ailt_finished or not current_item:
            record_data()
            
            st.badge(f"Your participant ID is: {ss.participant['id']}", icon= ':material/info:', color="grey")
            st.header("🏆 Training Complete!")

            _, main_col, __ = st.columns([1, 8, 1])

            with main_col:
                # White card container
                with st.container(border=True):
                    st.markdown("")
                    
                    # Left and right layout
                    left_col, right_col = st.columns([1, 2], vertical_alignment = "center")
                    
                    # Left column
                    with left_col:
                        st.image("images/llm_icon.png", width='stretch')
                    
                    # Right column 
                    with right_col:
                        st.markdown("<h3 style='text-align: center;'>Congratulations!</h3>", unsafe_allow_html=True)
                        st.markdown("<h2 style='text-align: center;'>You're now fully trained as an LLM!</h2>", unsafe_allow_html=True)
                        st.markdown("<h5 style='text-align: center;'>You've learned to:</h5>", unsafe_allow_html=True)
                        
                        # Create 2x2 grid
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.error(":material/translate: Understand and interpret language")
                            st.info(":material/edit: Generate fluent and coherent sentences")
     
                            
                        with col2:
                            st.warning(":material/school: Answer questions and respond in format")
                            st.success(":material/verified: Produce human-aligned outputs")


            col1, col2, col3 = st.columns([1, 1, 1])
            # with col2:
                # st.error("This is ___NOT THE END OF THE STUDY___.")
                # if st.button("Continue to the next task", type="primary", width='stretch'):
                #     try:
                #         # st.switch_page("pages/survey_te.py")
                #         st.switch_page("tool.py")
                #     except Exception:
                #         st.success("Training completed! You can now proceed to the next section.")
            st.stop()

        # If we're on a description screen, ONLY render that and nothing else
        if current_item['type'] == 'description':
            # Clear everything first
            st.empty()
            
            # Force clear any dialog states that might cause pop-ups
            ss.show_chat_dialog = False
            ss.dialog_closed = True
            ss.lock = False
            ss.showing_feedback = False
            
            # Clear any lingering question states
            for key in list(ss.keys()):
                if key.startswith('MC_opt_') or key.startswith('opt_'):
                    del ss[key]
            
            # ONLY render description screen and stop execution
            render_description_screen(current_item)
            st.stop()  

        st.html('''
            <style>
            div[aria-label="dialog"] > button[aria-label="Close"] {
                display: none !important;
            }
            </style>
            <script>
            function preventDialogClose() {
                const dialogs = document.querySelectorAll('div[data-testid="stDialog"]');
                dialogs.forEach(dialog => {
                    dialog.removeEventListener('click', preventOutsideClick);
                    dialog.addEventListener('click', preventOutsideClick, true);
                });
            }
            
            function preventOutsideClick(e) {
                const dialogContent = e.currentTarget.querySelector('div[role="dialog"]');
                if (dialogContent && !dialogContent.contains(e.target)) {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                    return false;
                }
            }
            
            document.addEventListener('DOMContentLoaded', function() {
                setTimeout(preventDialogClose, 100);
            });
            
            const observer = new MutationObserver(function(mutations) {
                mutations.forEach(function(mutation) {
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach(function(node) {
                            if (node.nodeType === 1 && node.querySelector && 
                                node.querySelector('div[data-testid="stDialog"]')) {
                                setTimeout(preventDialogClose, 100);
                            }
                        });
                    }
                });
            });
            
            observer.observe(document.body, { childList: true, subtree: true });
            </script>
            ''')

        # Show participant badge and phase title for content screens
        st.badge(f"Your participant ID is: {ss.participant['id']}", icon= ':material/info:', color="grey")
        names = {
            "PreTrain": "Pre-Training",
            "SFT": "Supervised Fine-Tuning",
            "RLHF": "Reinforcement Learning from Human Feedback"
        }
        st.title(f":material/modeling: {names[current_item['phase_name']]} Phase")

        # Render current screen based on sequence item type
        if current_item['type'] == 'question':
            render_question_screen(current_item)
        elif current_item['type'] == 'feedback':
            render_feedback_screen(current_item)

if __name__ == "__main__":
    main()