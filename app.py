import streamlit as st
import requests
import pandas as pd
import numpy as np
import json
import time
import io
import ast
from datetime import datetime

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Fluxx Reporting Portal", layout="wide")

st.title("📊 Fluxx Reporting Portal")
st.markdown("Automated Data Fetcher and Fiscal Year Reporter")

# --- SIDEBAR: API CREDENTIALS ---
with st.sidebar:
    st.header("1. API Authentication")
    st.info("Enter Fluxx API credentials to connect. These are required to sync fresh data.")
    client_site = st.text_input("Client Site URL", value='https://masscec.fluxx.io')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    
    st.divider()
    st.caption("Standardized Reporting Logic v3.0")

# --- CORE UTILITY FUNCTIONS ---

def get_auth_header():
    """Authenticates with Fluxx and returns the bearer token header."""
    try:
        url = f"{client_site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
        res = requests.post(url, data=data)
        if res.status_code != 200:
            st.error(f"Authentication failed: {res.text}")
            return None
        return {'Authorization': f"Bearer {res.json().get('access_token')}"}
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return None

def get_all_records(model, cols, headers, relations=None):
    """Handles Fluxx API pagination to retrieve the full dataset for a model."""
    all_recs = []
    page = 1
    base_url = client_site.rstrip('/') + '/api/rest/v2'
    cols_json = str(cols).replace("'", '"')
    params = {'per_page': 500, 'cols': cols_json}
    if relations: params['relation'] = json.dumps(relations)

    status_msg = st.empty()
    while True:
        params['page'] = page
        res = requests.get(f"{base_url}/{model}", headers=headers, params=params)
        if res.status_code != 200:
            st.warning(f"Error {res.status_code} on {model}")
            break
        data = res.json()
        records = data.get('records', [])
        if isinstance(records, dict): records = records.get(model, [])
        if not records: break
        all_recs.extend(records)
        status_msg.text(f"Syncing {model}... Page {page} ({len(all_recs)} records)")
        if page >= data.get('total_pages', 1): break
        page += 1
        time.sleep(0.3) 
    return pd.DataFrame(all_recs)

def clean_id(val):
    """Extracts ID from list-wrapped Fluxx fields."""
    if isinstance(val, list): return val[0] if len(val) > 0 else None
    return val

def clean_data_types(df):
    """Standardizes IDs into flat integers for reliable merging."""
    for col in df.columns:
        if 'id' in col.lower() and 'base' not in col.lower() and 'program_organization' not in col:
            df[col] = df[col].apply(clean_id)
    return df

def extract_org_name(val):
    """Extracts Grantee name from nested relational objects."""
    try:
        if isinstance(val, str) and not val.strip().startswith('['): return val
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict): return val[0].get('name', 'Unknown')
        if isinstance(val, str) and val.strip().startswith('['):
            val_list = ast.literal_eval(val)
            if len(val_list) > 0 and isinstance(val_list[0], dict): return val_list[0].get('name', 'Unknown')
    except: pass
    return 'Unknown'

# --- INTERFACE TABS ---
tab1, tab2 = st.tabs(["🚀 Step 1: Sync Data", "📄 Step 2: Generate Report"])

with tab1:
    st.header("Data Synchronization")
    st.write("Syncing will download the latest Reference, Budget, Grant, and Payment tables from Fluxx.")
    
    if st.button("Sync All Tables"):
        if not client_id or not client_secret:
            st.error("Please provide API credentials in the sidebar.")
        else:
            headers = get_auth_header()
            if headers:
                with st.status("Fetching live data...", expanded=True) as status:
                    # Automatically pull all necessary tables
                    get_all_records('program', ['id', 'name'], headers).to_csv('raw_program.csv', index=False)
                    get_all_records('sub_program', ['id', 'name'], headers).to_csv('raw_sub_program.csv', index=False)
                    get_all_records('funding_source', ['id', 'name', 'start_at', 'end_at'], headers).to_csv('raw_funding_source.csv', index=False)
                    get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers).to_csv('raw_fsa.csv', index=False)
                    
                    df_gr = get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, relations={"program_organization_id": ["name"]})
                    df_gr.to_csv('raw_grant_requests.csv', index=False)

                    get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers).to_csv('raw_split_rfs.csv', index=False)
                    get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers).to_csv('raw_payments_header.csv', index=False)
                    get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers).to_csv('raw_payment_splits.csv', index=False)
                    
                    status.update(label="Sync Successful!", state="complete")
                st.success("Data cache is now up to date.")

with tab2:
    st.header("Report Parameters")
    
    # Dynamically build the program list from synced data
    program_list = ["Run Step 1 First"]
    try:
        temp_prog = pd.read_csv('raw_program.csv')
        program_list = sorted(temp_prog['name'].unique().tolist())
    except: pass

    col_a, col_b = st.columns(2)
    with col_a:
        target_fy = st.number_input("Target Fiscal Year", value=2025)
    with col_b:
        target_program = st.selectbox("Program Filter", options=program_list)

    if st.button("Generate Report"):
        try:
            # 1. LOAD CACHED DATA
            df_prog = clean_data_types(pd.read_csv('raw_program.csv'))
            df_sub = clean_data_types(pd.read_csv('raw_sub_program.csv'))
            df_fund = clean_data_types(pd.read_csv('raw_funding_source.csv'))
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_grant_requests.csv')
            df_split_rfs = clean_data_types(pd.read_csv('raw_split_rfs.csv'))
            df_pay_head = clean_data_types(pd.read_csv('raw_payments_header.csv'))
            df_pay_split = clean_data_types(pd.read_csv('raw_payment_splits.csv'))

            # Prep Organization Names
            df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            df_gr = clean_data_types(df_gr)

            # Date Config
            fy_short = str(target_fy)[-2:]
            fy_start = pd.to_datetime(f"{target_fy - 1}-07-01")
            fy_end = pd.to_datetime(f"{target_fy}-06-30")
            months = pd.period_range(start=fy_start, end=fy_end, freq='M')

            # 2. DATA MERGING
            df_prog = df_prog.rename(columns={'id': 'p_id', 'name': 'Program'})
            df_sub = df_sub.rename(columns={'id': 'sp_id', 'name': 'Sub Focus Area'})
            df_fund = df_fund.rename(columns={'id': 'fs_id', 'name': 'Funding Source'})

            df_fsa = df_fsa[pd.to_numeric(df_fsa['spending_year']) == target_fy].copy()
            df_fsa = df_fsa.merge(df_prog, left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(df_sub, left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(df_fund, left_on='funding_source_id', right_on='fs_id', how='left')

            budget_col = f'Awards Budget Total FY{fy_short}'
            df_fsa = df_fsa.rename(columns={'amount': budget_col, 'id': 'FSA_ID'})
            
            df_gr = df_gr.rename(columns={'id': 'Request_ID', 'base_request_id': 'Request ID'})
            master = df_split_rfs.merge(df_gr, left_on='request_id', right_on='Request_ID', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master.rename(columns={'Grantee': 'Organization Name', 'project_title': 'Project Title'})

            # 3. CALCULATE MONTHLY & QUARTERLY TOTALS
            master['grant_agreement_at'] = pd.to_datetime(master['grant_agreement_at'], errors='coerce').dt.tz_localize(None)
            df_pay_head['due_at'] = pd.to_datetime(df_pay_head['due_at'], errors='coerce').dt.tz_localize(None)
            df_pay_full = df_pay_split.merge(df_pay_head, left_on='request_transaction_id', right_on='id', how='left')

            # Initialize Quarterly Columns
            for q in [1, 2, 3, 4]:
                master[f'Q{q} FY{fy_short} Awards'] = 0.0
                master[f'Q{q} FY{fy_short} Payments'] = 0.0

            all_time_cols = []
            for period in months:
                lbl = period.strftime('%b-%y')
                m_num = period.month
                # Assign Quarter (FY starts July = Q1)
                q = 1 if m_num in [7,8,9] else 2 if m_num in [10,11,12] else 3 if m_num in [1,2,3] else 4
                
                # Monthly Logic
                c_award, c_pay = f'{lbl} Awards', f'{lbl} Payments'
                master[c_award] = np.where(master['grant_agreement_at'].dt.to_period('M') == period, master['funding_amount'], 0.0)
                
                pays = df_pay_full[df_pay_full['due_at'].dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()
                master[c_pay] = master['id_x'].map(pays).fillna(0.0)
                
                all_time_cols.extend([c_award, c_pay])
                
                # Add to Quarter Total
                master[f'Q{q} FY{fy_short} Awards'] += master[c_award]
                master[f'Q{q} FY{fy_short} Payments'] += master[c_pay]

                # Insert Quarter Columns after the 3rd month of each quarter
                if m_num in [9, 12, 3, 6]:
                    all_time_cols.extend([f'Q{q} FY{fy_short} Awards', f'Q{q} FY{fy_short} Payments'])

            # 4. EXCEL EXPORT
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                report_df = master[master['Program'] == target_program] if target_program in master['Program'].values else master
                
                # Reorder columns to put time series at the end
                info_headers = ['Program', 'Sub Focus Area', 'Funding Source', budget_col, 'Organization Name', 'Request ID', 'Project Title']
                final_df = report_df[info_headers + all_time_cols]
                
                final_df.to_excel(writer, sheet_name="Program Report", index=False)
                
                workbook = writer.book
                worksheet = writer.sheets["Program Report"]
                
                # Formatting
                money_fmt = workbook.add_format({'num_format': '$#,##0.00'})
                header_fmt = workbook.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'border': 1})
                q_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'num_format': '$#,##0.00', 'border': 1})

                for col_num, value in enumerate(final_df.columns.values):
                    # Style Headers
                    worksheet.write(0, col_num, value, header_fmt)
                    # Apply money formatting to all numeric columns
                    if any(x in value for x in ['Awards', 'Payments', 'Budget']):
                        fmt = q_fmt if 'Q' in value else money_fmt
                        worksheet.set_column(col_num, col_num, 18, fmt)
                    else:
                        worksheet.set_column(col_num, col_num, 20)

            st.success("Report Generated!")
            st.download_button(
                label="📥 Download Excel Report",
                data=output.getvalue(),
                file_name=f"Fluxx_Report_{target_program}_FY{target_fy}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except Exception as e:
            st.error(f"Error: {e}")
