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
st.markdown("Automated Data Sync and Fiscal Year Reporting")

# --- SIDEBAR: API CREDENTIALS ---
with st.sidebar:
    st.header("1. API Authentication")
    st.info("Enter your Fluxx API credentials to allow the app to securely fetch your data.")
    client_site = st.text_input("Client Site URL", value='https://masscec.fluxx.io')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    
    st.divider()
    st.caption("Standardized Reporting Engine v6.0")

# --- UTILITY FUNCTIONS ---

def get_auth_header():
    try:
        url = f"{client_site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
        res = requests.post(url, data=data)
        if res.status_code != 200:
            st.error(f"Auth failed: {res.text}")
            return None
        return {'Authorization': f"Bearer {res.json().get('access_token')}"}
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return None

def get_all_records(model, cols, headers, relations=None):
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

# --- INTERFACE TABS ---
tab1, tab2 = st.tabs(["🚀 Step 1: Sync Data", "📄 Step 2: Generate Report"])

with tab1:
    st.header("Data Synchronization")
    st.write("Click 'Sync All Tables' to refresh the local cache with fresh data from Fluxx.")
    
    if st.button("Sync All Tables"):
        if not client_id or not client_secret:
            st.error("Please provide API credentials in the sidebar.")
        else:
            headers = get_auth_header()
            if headers:
                with st.status("Fetching live data from Fluxx...", expanded=True) as status:
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
                st.success("The local cache is now up to date.")

with tab2:
    st.header("Report Configuration")
    
    program_list = []
    try:
        temp_prog = pd.read_csv('raw_program.csv')
        # Only include specific programs found in the data
        program_list = sorted(temp_prog['name'].unique().tolist())
    except: 
        st.warning("Please run Step 1 to populate program list.")

    col_a, col_b = st.columns(2)
    with col_a:
        target_fy = st.number_input("Target Fiscal Year", value=2025)
    with col_b:
        target_program = st.selectbox("Filter by Program", options=program_list)

    if st.button("Generate Excel Report") and target_program:
        try:
            # 1. LOAD DATA
            df_prog = clean_data_types(pd.read_csv('raw_program.csv'))
            df_sub = clean_data_types(pd.read_csv('raw_sub_program.csv'))
            df_fund = clean_data_types(pd.read_csv('raw_funding_source.csv'))
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_grant_requests.csv')
            df_split_rfs = clean_data_types(pd.read_csv('raw_split_rfs.csv'))
            df_pay_head = clean_data_types(pd.read_csv('raw_payments_header.csv'))
            df_pay_split = clean_data_types(pd.read_csv('raw_payment_splits.csv'))

            df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            df_gr = clean_data_types(df_gr)
            fy_short = str(target_fy)[-2:]
            fy_start = pd.to_datetime(f"{target_fy-1}-07-01")
            fy_end = pd.to_datetime(f"{target_fy}-06-30")
            months = pd.period_range(start=fy_start, end=fy_end, freq='M')

            # 2. JOIN LOGIC
            df_prog = df_prog.rename(columns={'id': 'p_id', 'name': 'Program'})
            df_sub = df_sub.rename(columns={'id': 'sp_id', 'name': 'Sub Focus Area'})
            df_fund = df_fund.rename(columns={'id': 'fs_id', 'name': 'Funding Source'})

            df_fsa = df_fsa[pd.to_numeric(df_fsa['spending_year']) == target_fy].copy()
            df_fsa = df_fsa.merge(df_prog, left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(df_sub, left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(df_fund, left_on='funding_source_id', right_on='fs_id', how='left')

            budget_col = f'Awards Budget Total FY{fy_short}'
            df_fsa = df_fsa.rename(columns={'amount': budget_col, 'id': 'FSA_ID'})
            
            df_split_rfs = df_split_rfs.rename(columns={'id': 'RFS_ID_Key'})
            df_gr = df_gr.rename(columns={'id': 'Request_ID', 'base_request_id': 'Request ID'})
            master = df_split_rfs.merge(df_gr, left_on='request_id', right_on='Request_ID', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master.rename(columns={'Grantee': 'Organization Name', 'project_title': 'Project Title'})

            master['grant_agreement_at'] = pd.to_datetime(master['grant_agreement_at'], errors='coerce').dt.tz_localize(None)
            df_pay_head['due_at'] = pd.to_datetime(df_pay_head['due_at'], errors='coerce').dt.tz_localize(None)
            df_pay_full = df_pay_split.merge(df_pay_head, left_on='request_transaction_id', right_on='id', how='left')

            # Initialize Totals
            master[f'Awards Total FY{fy_short}'] = 0.0
            master[f'Payments Total FY{fy_short}'] = 0.0
            for q in [1, 2, 3, 4]:
                master[f'Q{q} FY{fy_short} Awards Total'] = 0.0
                master[f'Q{q} FY{fy_short} Payments Total'] = 0.0

            all_time_cols = []
            for period in months:
                lbl, m_num = period.strftime('%b-%y'), period.month
                q = 1 if m_num in [7,8,9] else 2 if m_num in [10,11,12] else 3 if m_num in [1,2,3] else 4
                c_aw, c_pa = f'{lbl} Awards', f'{lbl} Payments'
                master[c_aw] = np.where(master['grant_agreement_at'].dt.to_period('M') == period, master['funding_amount'], 0.0)
                pays = df_pay_full[df_pay_full['due_at'].dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()
                master[c_pa] = master['RFS_ID_Key'].map(pays).fillna(0.0)
                all_time_cols.append(c_aw)
                all_time_cols.append(c_pa)
                
                # Add to Quarter and Year totals
                master[f'Q{q} FY{fy_short} Awards Total'] += master[c_aw]
                master[f'Q{q} FY{fy_short} Payments Total'] += master[c_pa]
                master[f'Awards Total FY{fy_short}'] += master[c_aw]
                master[f'Payments Total FY{fy_short}'] += master[c_pa]
                
                if m_num in [9, 12, 3, 6]:
                    all_time_cols.append(f'Q{q} FY{fy_short} Awards Total')
                    all_time_cols.append(f'Q{q} FY{fy_short} Payments Total')

            # Append Year Totals at the end of the time series
            all_time_cols.append(f'Awards Total FY{fy_short}')
            all_time_cols.append(f'Payments Total FY{fy_short}')

            # 3. EXCEL CONSTRUCTION
            output = io.BytesIO()
            master = master[master['Program'] == target_program]
            
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                header_fmt = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
                subtotal_num_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'num_format': '$#,##0.00'})
                subtotal_txt_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3'})
                sub_prog_fmt = workbook.add_format({'bold': True, 'bg_color': '#ACB9CA', 'num_format': '$#,##0.00'})
                money_fmt = workbook.add_format({'num_format': '$#,##0.00'})

                sheet_name = str(target_program)[:31].replace('/', '-')
                final_rows = []
                info_cols = ['Program', 'Sub Focus Area', 'Funding Source', budget_col]
                grant_cols = ['Organization Name', 'Request ID', 'Project Title']
                all_cols = info_cols + ['DIV1'] + grant_cols + ['DIV2'] + all_time_cols

                for sub_prog, sub_group in master.groupby('Sub Focus Area'):
                    sub_totals = {c: 0.0 for c in all_time_cols + [budget_col]}
                    for fs_name, fs_group in sub_group.groupby('Funding Source'):
                        fs_totals = fs_group[all_time_cols].sum()
                        fs_budget = fs_group[budget_col].max()
                        first_row = True
                        for _, row in fs_group.iterrows():
                            rd = row.to_dict()
                            rd.update({'DIV1': "", 'DIV2': ""})
                            if not first_row: 
                                for c in info_cols: rd[c] = ""
                            rd['Row_Type'] = 'Data'
                            final_rows.append(rd)
                            first_row = False
                        
                        fs_sum_row = fs_totals.to_dict()
                        fs_sum_row.update({'DIV1': "", 'DIV2': "", 'Program': "", 'Sub Focus Area': "", 'Funding Source': f"TOTAL: {fs_name}", budget_col: fs_budget, 'Row_Type': 'FS_Subtotal'})
                        final_rows.append(fs_sum_row)
                        sub_totals[budget_col] += fs_budget
                        for c in all_time_cols: sub_totals[c] += fs_totals[c]

                    sp_sum_row = sub_totals.copy()
                    sp_sum_row.update({'DIV1': "", 'DIV2': "", 'Program': "", 'Sub Focus Area': f"TOTAL: {sub_prog.upper()}", 'Funding Source': "", 'Row_Type': 'SP_Total'})
                    final_rows.append(sp_sum_row)
                    final_rows.append({c: "" for c in all_cols})

                report_df = pd.DataFrame(final_rows)
                report_df[all_cols].to_excel(writer, sheet_name=sheet_name, index=False, startrow=1, header=False)
                worksheet = writer.sheets[sheet_name]

                for col_num, val in enumerate(all_cols):
                    if val in ['DIV1', 'DIV2']: worksheet.set_column(col_num, col_num, 1)
                    else: 
                        worksheet.write(0, col_num, val, header_fmt)
                        worksheet.set_column(col_num, col_num, 15)

                for r_idx, r_data in enumerate(final_rows):
                    e_row, r_type = r_idx + 1, r_data.get('Row_Type')
                    if r_type == 'FS_Subtotal':
                        worksheet.set_row(e_row, None, subtotal_txt_fmt)
                        worksheet.write(e_row, 3, r_data.get(budget_col), subtotal_num_fmt)
                        for i, c_name in enumerate(all_time_cols):
                            worksheet.write(e_row, len(info_cols) + 1 + len(grant_cols) + 1 + i, r_data.get(c_name), subtotal_num_fmt)
                    elif r_type == 'SP_Total':
                        worksheet.set_row(e_row, None, sub_prog_fmt)
                        worksheet.write(e_row, 3, r_data.get(budget_col), sub_prog_fmt)
                        for i, c_name in enumerate(all_time_cols):
                            worksheet.write(e_row, len(info_cols) + 1 + len(grant_cols) + 1 + i, r_data.get(c_name), sub_prog_fmt)

            st.success("Report Generated!")
            # Appending file name with program selected
            clean_name = "".join(x for x in target_program if x.isalnum() or x in " -_")
            st.download_button(
                label="📥 Download Excel Report", 
                data=output.getvalue(), 
                file_name=f"Fluxx_Report_FY{target_fy}_{clean_name}.xlsx"
            )
        except Exception as e:
            st.error(f"Error: {e}")
