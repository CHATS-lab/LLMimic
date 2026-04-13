import openai
import os
import json
from dotenv import load_dotenv
import pandas as pd
from pathlib import Path
import streamlit as st
import time
import base64
ss = st.session_state
class Tutor:
    def __init__(self,user_info):
        load_dotenv()
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = "gpt-4.1-2025-04-14"

        self.sys_prompt = self.load_sys_prompt(user_info)

        self.log = [] # interaction log

    def load_sys_prompt(self, user_info):
        base_path = Path.cwd() 
        prompt_path = base_path / "content" / "prompt.json"

        with prompt_path.open("r", encoding="utf-8") as file:
            prompt_json = json.load(file)
        prompt_json["PersonBase"] = prompt_json["PersonBase"].format(**user_info)
        return f"{prompt_json['base']}\n{prompt_json['PersonBase']}\n\n" + \
               "\n".join(f"{g}" for g in prompt_json.get("guideline", []))
    
    def llm_query(self, user_input, status=True, max_round=10):

        openai.api_key = self.api_key
    
        interaction_history = json.dumps(self.log, indent=2) if self.log else ""
        messages = [
            {"role": "system", "content": f"{self.sys_prompt}\n\nInteraction History:\n{interaction_history}" if self.log else self.sys_prompt},
            {"role": "user", "content": user_input}
        ]
        
        response = openai.chat.completions.create(
            model=self.model,
            messages=messages,
            max_completion_tokens=16384,
            temperature=1,
            stream=True
        )
        
        full_response = ""
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_response += content
                yield full_response
        
        # Log the complete interaction
        self.log.append({"user": user_input, "assistant": full_response})
        return full_response
def render_welcome_screen():
    """Render the welcome screen with information cards"""
    welcome_placeholder = st.empty()
    
    with welcome_placeholder.container():
        st.title(":material/lists: Task: _The Birth of a Language Model_")
        st.badge(f"Participant ID: {ss.participant['id']}", icon=":material/info:", color="grey")
        st.divider()
        
        # Information cards
        card1, card2 = st.columns(2)
        
        with card1:
            st.info("""
            ### :material/description: Description
            #### • Role-play to learn how a :red[Large Language Model (LLM)], :red[such as ChatGPT], :red[is trained].
            #### • Explore **three key phases** in the LLM development and training process.
            #### • Get **hands-on exposure** to the training data behind the model's capabilities.
            #### • **You will take pop-up quizzes along the way to test your learning.**
            """)


        with card2:
            st.warning("""
            ### :material/support_agent: AI Tutor
            #### • You are encouraged, but not required, to ask the AI Tutor questions during the activity.
            #### • Click the :red-background[**Close Chat**] button to hide the Tutor interface in case of any technical errors.
            """)
            col1, col2, col3 = st.columns([1,3,1])
            picture_path = Path("images/train_diagram.png")
            with col2:
                if picture_path.exists():
                    st.image("images/train_diagram.png", width='stretch')
        
        # Start button
        st.divider()
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            st.markdown(":red[⚠️ If you experience technical issues, don't ___close your browser or refresh the page___. Use :red-background[Close Chat] to exit the Tutor safely, and :red-background[Previous] and :red-background[Next] to navigate between tasks.]")
            if st.button("Let's Start!", type="primary", width='stretch'):
                welcome_placeholder.empty()
                ss.ail_train_welcome = True
                ss.show_description = True
                if 'show_description_on_return' in ss:
                    del ss['show_description_on_return']
                st.rerun()
def load_data(path):
    with open(path) as f:
        return json.load(f)
    
def render_progress_panel(current_item):
    """Render the progress panel on the right"""
    phase_name = current_item['phase_name']
    phase_path = current_item['phase_path']
    
    # Load phase data to get total questions
    data = load_data(phase_path)
    total_questions = len(data["questions"])
    current_q = current_item['q_idx'] + 1
    
    st.markdown(f"**Progress of Current Phase: :red-background[{current_q}] of {total_questions}**")
    
    progress_value = current_q / total_questions
    st.markdown("""
        <style>
        div[data-testid="stProgress"] > div > div > div > div {
            background-color: #F44336;
        }
        </style>
        """, unsafe_allow_html=True)
    st.progress(progress_value)
    
    # Chart
    if phase_name in ["PreTrain", "SFT"]:
        y_label = "Loss"
        chart_title = ":material/trending_down: Loss Over Time"
    else:
        y_label = "Reward"
        chart_title = ":material/trending_up: Reward Over Time"
    
    st.markdown(f"### {chart_title}")
    current_vals = ss.loss_tracking.get(phase_name, [])
    
    if len(current_vals) > 0:
        chart_data = pd.DataFrame({
            'Epoch': range(len(current_vals)),
            y_label: current_vals
        })
        if len(current_vals) > 1:
            current_val = current_vals[-1]
            prev_val = current_vals[-2]
            delta = current_val - prev_val
            st.metric(
                label=f"Current {y_label}",
                value=f"{current_val:.3f}",
                delta=f"{delta:+.3f}",
                delta_color="inverse" if not phase_name=="RLHF" else "normal",
            )
        st.line_chart(
            chart_data.set_index('Epoch'), 
            width='stretch', 
            height=300,
            x_label="Epoch",
            y_label=y_label
        )
        
        
    else:
        st.info(f"📊 {y_label} tracking will appear as you progress")
@st.dialog("🤖 AI Tutor Chat", width="large")
def chat_dialog():
    """Handle the AI tutor chat dialog"""
    # Initialize agent if not exists
    if "tutor" not in ss:
        part = ss.get('participant', {})
        user = {"age": part.get('age'), "degree": part.get('education'), "field": part.get('field')}
        ss.tutor = Tutor(user)

    # Initialize chat states
    chat_states = {
        "chat_log": [],
        "processing_input": False,
        "streaming_response": "",
        "stream_complete": False
    }
    
    for key, default_value in chat_states.items():
        if key not in ss:
            ss[key] = default_value

    # Welcome message
    st.markdown("👋 Ask me anything about this task or the concepts you're learning!")
    
    # Chat history with scrollable container
    chat_container = st.container(height=400)
    with chat_container:
        # Display all completed messages
        for msg in ss.chat_log:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        
        # Display streaming response if processing
        if ss.processing_input and ss.streaming_response:
            with st.chat_message("assistant"):
                st.markdown(ss.streaming_response)
    
    # Handle stream completion
    if ss.stream_complete:
        ss.chat_log.append({"role": "assistant", "content": ss.streaming_response})
        
        # Reset all states
        cleanup_keys = ["processing_input", "streaming_response", "stream_complete", 
                       "current_user_input", "response_generator"]
        for key in cleanup_keys:
            if key in ss:
                del ss[key]
        
        st.rerun()
    
    # Input handling
    if not ss.processing_input:
        user_input = st.chat_input("Type your question here...", key="chat_input_field")
        
        if user_input:
            ss.chat_log.append({"role": "user", "content": user_input})
            ss.processing_input = True
            ss.streaming_response = ""
            ss.current_user_input = user_input
            ss.stream_complete = False
            ss.response_generator = ss.tutor.llm_query(user_input)
            st.rerun()
    else:
        # Show disabled input while processing
        st.chat_input("🤖 Tutor is thinking... Please wait", disabled=True, key="chat_input_disabled")
        
        # Process the streaming response
        try:
            if hasattr(ss, 'response_generator') and ss.response_generator:
                try:
                    partial_response = next(ss.response_generator)
                    ss.streaming_response = partial_response
                    st.rerun()
                except StopIteration:
                    ss.stream_complete = True
                    st.rerun()
            else:
                ss.response_generator = ss.tutor.llm_query(ss.current_user_input)
                st.rerun()
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            ss.chat_log.append({"role": "assistant", "content": error_msg})
            
            # Clean up on error
            cleanup_keys = ["processing_input", "streaming_response", "stream_complete", 
                           "current_user_input", "response_generator"]
            for key in cleanup_keys:
                if key in ss:
                    del ss[key]
            st.rerun()
    
    st.markdown("⚠️ Please click the :red-background[**Close Chat**] button to hide the Tutor interface in case of any technical errors.")
    if st.button("Close Chat", type="primary", width='stretch', key="close_chat_btn"):
        ss.show_chat_dialog = False
        ss.dialog_closed = True
        
        # Clean up all chat states
        cleanup_keys = ["processing_input", "streaming_response", "current_user_input", 
                       "response_generator", "stream_complete"]
        for key in cleanup_keys:
            if key in ss:
                del ss[key]
        st.rerun()

def stream_write(description):
    """Unified function to stream text and display images in correct order"""
    for i, item in enumerate(description):
        if item.strip():
            # Check if this is a diagram marker
            if item.strip().startswith('[diagram'):
                # Extract diagram name from [diagram:filename] format
                if ':' in item.strip():
                    diagram_file = item.strip().split(':')[1].rstrip(']')
                    diagram_file += '.gif'
                
                # Display image using columns
                image_path = Path(f"images/{diagram_file}")
                if image_path.exists():
                    left_col, img_col, right_col = st.columns([1, 2, 1])
                    with img_col:
                        # Read and encode image
                        with open(image_path, "rb") as img_file:
                            img_data = base64.b64encode(img_file.read()).decode()
                        st.markdown(
                            f'<img src="data:image/gif;base64,{img_data}" style="width: 80%; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">',
                            unsafe_allow_html=True
                        )
                else:
                    st.warning(f"Image not found: {image_path}")
            else:
                # This is a text sentence - create a generator for streaming
                def text_generator():
                    yield "### "
                    words = item.split()
                    for word in words:
                        if word:
                            yield word + " "
                            time.sleep(0.1)
                
                # Stream the text
                st.write_stream(text_generator())
            
            # Add spacing between items (except after last item)
            if i < len(description) - 1:
                st.write("")
                time.sleep(0.8)