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
    client_site = st.text_input("Client Site URL", value='')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    st.divider()
    st.caption("Standardized Reporting Engine v13.0")

# --- UTILITY FUNCTIONS ---
def get_auth_header():
    try:
        url = f"{client_site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
        res = requests.post(url, data=data)
        return {'Authorization': f"Bearer {res.json().get('access_token')}"} if res.status_code == 200 else None
    except: return None

def get_all_records(model, cols, headers, relations=None):
    all_recs, page, base_url = [], 1, client_site.rstrip('/') + '/api/rest/v2'
    params = {'per_page': 500, 'cols': str(cols).replace("'", '"')}
    if relations: params['relation'] = json.dumps(relations)
    status_msg = st.empty()
    while True:
        params['page'] = page
        res = requests.get(f"{base_url}/{model}", headers=headers, params=params)
        if res.status_code != 200: break
        data = res.json()
        records = data.get('records', [])
        if isinstance(records, dict): records = records.get(model, [])
        if not records: break
        all_recs.extend(records)
        status_msg.text(f"Syncing {model}... Page {page}")
        if page >= data.get('total_pages', 1): break
        page += 1
        time.sleep(0.3) 
    return pd.DataFrame(all_recs)

def clean_data_types(df):
    for col in df.columns:
        if 'id' in col.lower() and 'base' not in col.lower() and 'program_organization' not in col:
            df[col] = df[col].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
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

tab1, tab2 = st.tabs(["🚀 Step 1: Sync Data", "📄 Step 2: Generate Report"])

with tab1:
    if st.button("Sync All Tables"):
        headers = get_auth_header()
        if headers:
            with st.status("Syncing...") as s:
                get_all_records('program', ['id', 'name'], headers).to_csv('raw_program.csv', index=False)
                get_all_records('sub_program', ['id', 'name'], headers).to_csv('raw_sub_program.csv', index=False)
                get_all_records('funding_source', ['id', 'name'], headers).to_csv('raw_funding_source.csv', index=False)
                get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers).to_csv('raw_fsa.csv', index=False)
                get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, relations={"program_organization_id": ["name"]}).to_csv('raw_grant_requests.csv', index=False)
                get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers).to_csv('raw_split_rfs.csv', index=False)
                get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers).to_csv('raw_payments_header.csv', index=False)
                get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers).to_csv('raw_payment_splits.csv', index=False)
                s.update(label="Sync Successful!", state="complete")

with tab2:
    program_list = []
    try:
        program_list = sorted(pd.read_csv('raw_program.csv')['name'].unique().tolist())
    except: pass

    target_fy = st.number_input("Target Fiscal Year", value=2025)
    target_program = st.selectbox("Filter by Program", options=program_list)

    if st.button("Generate Excel Report") and target_program:
        try:
            # 1. LOAD & JOIN
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_grant_requests.csv')
            df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            
            fy_short = str(target_fy)[-2:]
            months = pd.period_range(start=f"{target_fy-1}-07-01", end=f"{target_fy}-06-30", freq='M')

            df_fsa = df_fsa[pd.to_numeric(df_fsa['spending_year']) == target_fy].copy()
            df_fsa = df_fsa.merge(pd.read_csv('raw_program.csv').rename(columns={'id':'p_id','name':'Program'}), left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(pd.read_csv('raw_sub_program.csv').rename(columns={'id':'sp_id','name':'Sub Focus Area'}), left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(pd.read_csv('raw_funding_source.csv').rename(columns={'id':'fs_id','name':'Funding Source'}), left_on='funding_source_id', right_on='fs_id', how='left')

            budget_col = f'Awards Budget Total FY{fy_short}'
            df_fsa = df_fsa.rename(columns={'amount': budget_col, 'id': 'FSA_ID'})
            master = clean_data_types(pd.read_csv('raw_split_rfs.csv')).rename(columns={'id':'RFS_ID_Key'}).merge(df_gr.rename(columns={'id':'Request_ID','base_request_id':'Request ID'}), left_on='request_id', right_on='Request_ID', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master.rename(columns={'Grantee': 'Organization Name', 'project_title': 'Project Title'})
            master = master[master['Program'] == target_program].copy()

            # 2. TIME SERIES CALCS
            df_pay = pd.read_csv('raw_payment_splits.csv').merge(pd.read_csv('raw_payments_header.csv'), left_on='request_transaction_id', right_on='id', how='left')
            all_time_cols = []
            for period in months:
                lbl, m_num = period.strftime('%b-%y'), period.month
                q = 1 if m_num in [7,8,9] else 2 if m_num in [10,11,12] else 3 if m_num in [1,2,3] else 4
                c_aw, c_pa = f'{lbl} Awards', f'{lbl} Payments'
                master[c_aw] = np.where(pd.to_datetime(master['grant_agreement_at']).dt.to_period('M') == period, master['funding_amount'], 0.0)
                master[c_pa] = master['RFS_ID_Key'].map(df_pay[pd.to_datetime(df_pay['due_at']).dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()).fillna(0.0)
                all_time_cols.extend([c_aw, c_pa])
                if m_num in [9, 12, 3, 6]: all_time_cols.extend([f'Q{q} FY{fy_short} Awards Total', f'Q{q} FY{fy_short} Payments Total'])
            
            # 3. EXCEL CONSTRUCTION
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                # FORMATS
                header_f = workbook.add_format({'bold':True, 'bg_color':'#4472C4', 'font_color':'white', 'border':1, 'align':'center'})
                money_f = workbook.add_format({'num_format':'$#,##0.00', 'border':1})
                text_f = workbook.add_format({'border':1})
                sub_f = workbook.add_format({'bold':True, 'bg_color':'#D3D3D3', 'num_format':'$#,##0.00', 'border':1})
                sub_t_f = workbook.add_format({'bold':True, 'bg_color':'#D3D3D3', 'border':1})
                sp_f = workbook.add_format({'bold':True, 'bg_color':'#ACB9CA', 'num_format':'$#,##0.00', 'border':1})
                div_f = workbook.add_format({'bg_color':'#808080', 'border':1})

                info_cols = ['Program', 'Sub Focus Area', 'Funding Source', budget_col]
                grant_cols = ['Organization Name', 'Request ID', 'Project Title']
                all_cols = info_cols + ['DIV1'] + grant_cols + ['DIV2'] + all_time_cols

                final_rows = []
                for sp, sp_grp in master.groupby('Sub Focus Area'):
                    for fs, fs_grp in sp_grp.groupby('Funding Source'):
                        for i, (_, row) in enumerate(fs_grp.iterrows()):
                            rd = row.to_dict()
                            if i > 0: rd.update({c:"" for c in info_cols})
                            rd.update({'DIV1':"", 'DIV2':"", 'Row_Type':'Data'})
                            final_rows.append(rd)
                        fs_sum = fs_grp[all_time_cols].sum().to_dict()
                        fs_sum.update({'DIV1':"", 'DIV2':"", 'Funding Source':f"TOTAL: {fs}", budget_col:fs_grp[budget_col].max(), 'Row_Type':'FS'})
                        final_rows.append(fs_sum)
                    sp_sum = sp_grp[all_time_cols].sum().to_dict()
                    sp_sum.update({'DIV1':"", 'DIV2':"", 'Sub Focus Area':f"TOTAL: {sp.upper()}", budget_col:sp_grp[budget_col].sum(), 'Row_Type':'SP'})
                    final_rows.append(sp_sum)
                    final_rows.append({c:"" for c in all_cols})

                sheet = writer.book.add_worksheet(str(target_program)[:31])
                for col_num, col_name in enumerate(all_cols):
                    sheet.write(0, col_num, col_name, header_f)
                    width = 2 if "DIV" in col_name else 18
                    sheet.set_column(col_num, col_num, width)

                for r_idx, r_data in enumerate(final_rows):
                    e_row, r_type = r_idx + 1, r_data.get('Row_Type')
                    curr_fmt = sp_f if r_type == 'SP' else sub_f if r_type == 'FS' else money_f
                    txt_fmt = sp_f if r_type == 'SP' else sub_t_f if r_type == 'FS' else text_f
                    
                    for c_idx, col in enumerate(all_cols):
                        val = r_data.get(col, "")
                        if "DIV" in col:
                            sheet.write(e_row, c_idx, "", div_f)
                        elif any(x in col for x in ['Awards', 'Payments', 'Budget']):
                            sheet.write(e_row, c_idx, pd.to_numeric(val, errors='coerce') or 0, curr_fmt)
                        else:
                            sheet.write(e_row, c_idx, str(val), txt_fmt)

                sheet.freeze_panes(1, 8)

            st.download_button("📥 Download Excel Report", output.getvalue(), f"Report_FY{target_fy}.xlsx")
        except Exception as e: st.error(f"Error: {e}")
