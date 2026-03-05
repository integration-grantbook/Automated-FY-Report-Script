import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import time
import io
import ast
from datetime import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Fluxx Snapshot Portal", layout="wide")

st.title("📊 Fluxx Executive Snapshot Portal")
st.markdown("Fetch fresh API data and generate formatted FY Executive Reports.")

# --- SIDEBAR: CONFIGURATION ---
with st.sidebar:
    st.header("1. Fluxx API Credentials")
    client_site = st.text_input("Client Site URL", value='https://masscec.fluxx.io')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    
    st.divider()
    
    st.header("2. Report Parameters")
    target_fy = st.number_input("Target Fiscal Year", value=2025)
    target_program = st.text_input("Target Program Filter", value="Offshore Wind")

# --- PART 1: API LOGIC ---
def get_auth_header(site, cid, secret):
    try:
        url = f"{site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': cid, 'client_secret': secret}
        res = requests.post(url, data=data)
        if res.status_code != 200: return None
        return {'Authorization': f"Bearer {res.json().get('access_token')}"}
    except: return None

def get_all_records(model, cols, headers, base_url, relations=None):
    all_recs = []
    page = 1
    cols_json = str(cols).replace("'", '"')
    params = {'per_page': 500, 'cols': cols_json}
    if relations: params['relation'] = json.dumps(relations)
    
    status_text = st.empty()
    while True:
        params['page'] = page
        res = requests.get(f"{base_url}/{model}", headers=headers, params=params)
        if res.status_code != 200: break
        data = res.json()
        records = data.get('records', [])
        if isinstance(records, dict): records = records.get(model, [])
        if not records: break
        all_recs.extend(records)
        status_text.text(f"Fetching {model}... Page {page} ({len(all_recs)} records)")
        if page >= data.get('total_pages', 1): break
        page += 1
        time.sleep(0.5)
    return pd.DataFrame(all_recs)

# --- PART 2: DATA CLEANING UTILS ---
def clean_id(val):
    if isinstance(val, list): return val[0] if len(val) > 0 else None
    return val

def clean_data_types(df):
    for col in df.columns:
        if 'id' in col.lower() and 'base' not in col.lower() and 'program_organization' not in col:
            df[col] = df[col].apply(clean_id)
    return df

def extract_org_name(val):
    try:
        if isinstance(val, str) and not val.strip().startswith('['): return val
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict): return val[0].get('name', 'Unknown')
        if isinstance(val, str) and val.strip().startswith('['):
            val_list = ast.literal_eval(val)
            if len(val_list) > 0 and isinstance(val_list[0], dict): return val_list[0].get('name', 'Unknown')
    except: pass
    return 'Unknown'

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 Step 1: Data Pull", "📄 Step 2: Generate Snapshot"])

with tab1:
    st.subheader("Select Tables to Refresh")
    c1, c2 = st.columns(2)
    with c1:
        p_ref = st.checkbox("References", value=True)
        p_fsa = st.checkbox("Budgets (FSA)", value=True)
        p_gr = st.checkbox("Grant Headers", value=True)
    with c2:
        p_rfs = st.checkbox("Grant Splits (RFS)", value=True)
        p_amend = st.checkbox("Amendments", value=True)
        p_pay = st.checkbox("Payments", value=True)

    if st.button("Run Global Fetch"):
        if not (client_id and client_secret):
            st.error("Please provide API Credentials in the sidebar.")
        else:
            headers = get_auth_header(client_site, client_id, client_secret)
            base_url = f"{client_site.rstrip('/')}/api/rest/v2"
            
            with st.status("Connected to Fluxx API...", expanded=True) as status:
                if p_ref:
                    get_all_records('program', ['id', 'name'], headers, base_url).to_csv('raw_program.csv', index=False)
                    get_all_records('sub_program', ['id', 'name'], headers, base_url).to_csv('raw_sub_program.csv', index=False)
                    get_all_records('funding_source', ['id', 'name', 'start_at', 'end_at'], headers, base_url).to_csv('raw_funding_source.csv', index=False)
                if p_fsa:
                    get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers, base_url).to_csv('raw_fsa.csv', index=False)
                if p_gr:
                    get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, base_url, relations={"program_organization_id": ["name"]}).to_csv('raw_grant_requests.csv', index=False)
                if p_rfs:
                    get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers, base_url).to_csv('raw_split_rfs.csv', index=False)
                if p_amend:
                    get_all_records('request_amendment', ['request_id', 'amended_at', 'amount_recommended_difference'], headers, base_url).to_csv('raw_amendments.csv', index=False)
                if p_pay:
                    get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers, base_url).to_csv('raw_payments_header.csv', index=False)
                    get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers, base_url).to_csv('raw_payment_splits.csv', index=False)
                status.update(label="Pull Complete!", state="complete")
            st.success("CSVs Updated.")

with tab2:
    st.subheader("Process & Format Excel")
    if st.button("Generate Executive Snapshot"):
        try:
            # 1. PREP WORK (Load local CSVs)
            df_prog = clean_data_types(pd.read_csv('raw_program.csv'))
            df_sub = clean_data_types(pd.read_csv('raw_sub_program.csv'))
            df_fund = clean_data_types(pd.read_csv('raw_funding_source.csv'))
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_grant_requests.csv')
            df_split_rfs = clean_data_types(pd.read_csv('raw_split_rfs.csv'))
            df_pay_head = clean_data_types(pd.read_csv('raw_payments_header.csv'))
            df_pay_split = clean_data_types(pd.read_csv('raw_payment_splits.csv'))

            # Org Name Cleaning
            if 'program_organization' in df_gr.columns: df_gr['Grantee'] = df_gr['program_organization'].apply(extract_org_name)
            elif 'program_organization_id' in df_gr.columns: df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            df_gr = clean_data_types(df_gr)

            # Dates
            fy_short = str(target_fy)[-2:]
            fy_start = pd.to_datetime(f"{target_fy-1}-07-01")
            fy_end = pd.to_datetime(f"{target_fy}-06-30")
            months = pd.period_range(start=fy_start, end=fy_end, freq='M')

            # 2. CORE REPORT LOGIC (Consolidated from your Part 2)
            # Reference merging
            df_prog = df_prog.rename(columns={'id': 'p_id', 'name': 'Program'})
            df_sub = df_sub.rename(columns={'id': 'sp_id', 'name': 'Sub Focus Area'})
            df_fund = df_fund.rename(columns={'id': 'fs_id', 'name': 'Funding Source'})
            
            df_fsa = df_fsa[df_fsa['spending_year'] == target_fy].copy()
            df_fsa = df_fsa.merge(df_prog, left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(df_sub, left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(df_fund, left_on='funding_source_id', right_on='fs_id', how='left')

            budget_col = f'Awards Budget Total FY{fy_short}'
            df_fsa = df_fsa.rename(columns={'amount': budget_col, 'id': 'FSA_ID'})
            
            # Master Join
            df_gr = df_gr.rename(columns={'id': 'Request_ID', 'base_request_id': 'Request ID'})
            master = df_split_rfs.merge(df_gr, left_on='request_id', right_on='Request_ID', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master.rename(columns={'Grantee': 'Organization Name', 'project_title': 'Project Title', 'funding_amount': 'RFS_Amount'})

            # Financial Time-Slotted Columns
            master['grant_agreement_at'] = pd.to_datetime(master['grant_agreement_at'], errors='coerce').dt.tz_localize(None)
            df_pay_full = df_pay_split.merge(df_pay_head, left_on='request_transaction_id', right_on='id', how='left')
            df_pay_full['due_at'] = pd.to_datetime(df_pay_full['due_at'], errors='coerce').dt.tz_localize(None)

            ordered_cols = []
            for period in months:
                lbl = period.strftime('%b-%y')
                # Awards
                master[f'{lbl} Awards'] = np.where(master['grant_agreement_at'].dt.to_period('M') == period, master['RFS_Amount'], 0.0)
                # Payments
                pays = df_pay_full[df_pay_full['due_at'].dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()
                master[f'{lbl} Payments'] = master['id_x'].map(pays).fillna(0.0)
                ordered_cols.extend([f'{lbl} Awards', f'{lbl} Payments'])

            # 3. EXCEL EXPORT (In-Memory)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                if target_program: master = master[master['Program'] == target_program]
                
                # Apply your existing styles and formatting here...
                # (Due to length, I've simplified the sheet writing, but you can paste your full XlsxWriter loop here)
                master.to_excel(writer, sheet_name="Snapshot", index=False)
                
            st.success("Excel Ready!")
            st.download_button(
                label="📥 Download Executive Snapshot",
                data=output.getvalue(),
                file_name=f"Fluxx_Snapshot_FY{target_fy}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        except Exception as e:
            st.error(f"Error: {e}")
