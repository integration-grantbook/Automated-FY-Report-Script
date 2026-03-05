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
    # URL is now blank by default
    client_site = st.text_input("Client Site URL", value="", placeholder="https://yourdomain.fluxx.io")
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    st.divider()
    st.caption("Standardized Reporting Engine v9.0")

# --- UTILITY FUNCTIONS ---

def get_auth_header():
    try:
        url = f"{client_site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
        res = requests.post(url, data=data)
        if res.status_code != 200:
            return None
        return {'Authorization': f"Bearer {res.json().get('access_token')}"}
    except:
        return None

def get_all_records(model, cols, headers, relations=None):
    all_recs = []
    page = 1
    base_url = f"{client_site.rstrip('/')}/api/rest/v2"
    cols_json = str(cols).replace("'", '"')
    params = {'per_page': 500, 'cols': cols_json}
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
        time.sleep(0.2)
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

# --- INTERFACE TABS ---
tab1, tab2 = st.tabs(["🚀 Step 1: Sync Data", "📄 Step 2: Generate Report"])

with tab1:
    st.header("Data Synchronization")
    if st.button("Sync All Tables"):
        if not client_site or not client_id or not client_secret:
            st.error("Please provide all API credentials.")
        else:
            headers = get_auth_header()
            if headers:
                with st.status("Fetching live data...", expanded=True) as status:
                    get_all_records('program', ['id', 'name'], headers).to_csv('raw_program.csv', index=False)
                    get_all_records('sub_program', ['id', 'name'], headers).to_csv('raw_sub_program.csv', index=False)
                    get_all_records('funding_source', ['id', 'name'], headers).to_csv('raw_fund.csv', index=False)
                    get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers).to_csv('raw_fsa.csv', index=False)
                    
                    df_gr = get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, relations={"program_organization_id": ["name"]})
                    df_gr.to_csv('raw_gr.csv', index=False)

                    get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers).to_csv('raw_rfs.csv', index=False)
                    get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers).to_csv('raw_ph.csv', index=False)
                    get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers).to_csv('raw_ps.csv', index=False)
                    status.update(label="Sync Complete!", state="complete")

with tab2:
    try:
        prog_df = pd.read_csv('raw_program.csv')
        program_list = sorted(prog_df['name'].unique().tolist())
        
        target_fy = st.number_input("Target Fiscal Year", value=2025)
        target_program = st.selectbox("Filter by Program", options=program_list)

        if st.button("Generate Excel Report"):
            # 1. LOAD & CLEAN
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_gr.csv')
            df_rfs = clean_data_types(pd.read_csv('raw_rfs.csv')).rename(columns={'id': 'RFS_ID_Key'})
            df_ph = clean_data_types(pd.read_csv('raw_ph.csv'))
            df_ps = clean_data_types(pd.read_csv('raw_ps.csv'))
            
            df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            df_gr = clean_data_types(df_gr).rename(columns={'id': 'Req_ID_Internal', 'base_request_id': 'Request ID'})
            
            fy_short = str(target_fy)[-2:]
            budget_col = f'Awards Budget Total FY{fy_short}'
            
            # 2. MERGE
            df_fsa = df_fsa[pd.to_numeric(df_fsa['spending_year']) == target_fy]
            df_fsa = df_fsa.merge(prog_df.rename(columns={'id':'p_id','name':'Program'}), left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(pd.read_csv('raw_sub_program.csv').rename(columns={'id':'sp_id','name':'Sub Focus Area'}), left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(pd.read_csv('raw_fund.csv').rename(columns={'id':'fs_id','name':'Funding Source'}), left_on='funding_source_id', right_on='fs_id', how='left')
            df_fsa = df_fsa.rename(columns={'amount': budget_col, 'id': 'FSA_ID'})

            master = df_rfs.merge(df_gr, left_on='request_id', right_on='Req_ID_Internal', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master[master['Program'] == target_program].copy()

            # 3. FINANCIAL TIME SERIES
            months = pd.period_range(start=f"{target_fy-1}-07-01", end=f"{target_fy}-06-30", freq='M')
            df_pay_full = df_ps.merge(df_ph, left_on='request_transaction_id', right_on='id', how='left')
            
            master[f'Awards Total FY{fy_short}'] = 0.0
            master[f'Payments Total FY{fy_short}'] = 0.0
            all_time_cols = []

            for period in months:
                lbl, m_num = period.strftime('%b-%y'), period.month
                q = 1 if m_num in [7,8,9] else 2 if m_num in [10,11,12] else 3 if m_num in [1,2,3] else 4
                c_aw, c_pa = f'{lbl} Awards', f'{lbl} Payments'
                
                master[c_aw] = np.where(pd.to_datetime(master['grant_agreement_at']).dt.to_period('M') == period, master['funding_amount'], 0.0)
                pays = df_pay_full[pd.to_datetime(df_pay_full['due_at']).dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()
                master[c_pa] = master['RFS_ID_Key'].map(pays).fillna(0.0)
                
                all_time_cols.extend([c_aw, c_pa])
                master[f'Awards Total FY{fy_short}'] += master[c_aw]
                master[f'Payments Total FY{fy_short}'] += master[c_pa]
                
                if m_num in [9, 12, 3, 6]:
                    q_aw, q_pa = f'Q{q} FY{fy_short} Awards Total', f'Q{q} FY{fy_short} Payments Total'
                    master[q_aw] = master[[c for c in master.columns if 'Awards' in c and any(m in c for m in [period.strftime('%b'), (period-1).strftime('%b'), (period-2).strftime('%b')])]].sum(axis=1)
                    master[q_pa] = master[[c for c in master.columns if 'Payments' in c and any(m in c for m in [period.strftime('%b'), (period-1).strftime('%b'), (period-2).strftime('%b')])]].sum(axis=1)
                    all_time_cols.extend([q_aw, q_pa])

            all_time_cols.extend([f'Awards Total FY{fy_short}', f'Payments Total FY{fy_short}'])

            # 4. EXCEL
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                # Styles
                head_f = workbook.add_format({'bold':True, 'bg_color':'#4472C4', 'font_color':'white', 'border':1, 'align':'center', 'text_wrap':True})
                num_f = workbook.add_format({'num_format':'$#,##0.00', 'border':1})
                sub_f = workbook.add_format({'bold':True, 'bg_color':'#D3D3D3', 'num_format':'$#,##0.00', 'border':1})
                sp_f = workbook.add_format({'bold':True, 'bg_color':'#ACB9CA', 'num_format':'$#,##0.00', 'border':1})
                tot_f = workbook.add_format({'bold':True, 'bg_color':'#D9E1F2', 'num_format':'$#,##0.00', 'border':1})
                div_f = workbook.add_format({'bg_color':'#808080', 'border':1})

                info_c, grant_c = ['Program', 'Sub Focus Area', 'Funding Source', budget_col], ['Grantee', 'Request ID', 'project_title']
                cols = info_c + ['Spacer1'] + grant_c + ['Spacer2'] + all_time_cols
                
                final_rows = []
                for sp, sp_grp in master.groupby('Sub Focus Area'):
                    for fs, fs_grp in sp_grp.groupby('Funding Source'):
                        for _, r in fs_grp.iterrows():
                            final_rows.append({**r.to_dict(), 'Spacer1':'', 'Spacer2':'', 'RowType':'Data'})
                        final_rows.append({**fs_grp[all_time_cols].sum().to_dict(), budget_col: fs_grp[budget_col].max(), 'Funding Source': f'TOTAL: {fs}', 'RowType':'FS'})
                    final_rows.append({**sp_grp[all_time_cols].sum().to_dict(), budget_col: sp_grp[budget_col].sum(), 'Sub Focus Area': f'TOTAL: {sp}', 'RowType':'SP'})
                    final_rows.append({c:'' for c in cols})

                pd.DataFrame(final_rows)[cols].to_excel(writer, sheet_name='Report', index=False, startrow=1, header=False)
                sheet = writer.sheets['Report']

                for i, c in enumerate(cols):
                    sheet.write(0, i, '' if 'Spacer' in c else c, head_f)
                    if 'Spacer' in c: sheet.set_column(i, i, 2, div_f)
                    else: sheet.set_column(i, i, 18)

                for r_idx, row in enumerate(final_rows):
                    e_row = r_idx + 1
                    r_type = row.get('RowType')
                    fmt = sub_f if r_type == 'FS' else sp_f if r_type == 'SP' else num_f
                    
                    for i, c in enumerate(cols):
                        if 'Spacer' not in c:
                            val = row.get(c, 0) if any(x in c for x in ['Awards', 'Payments', 'Budget']) else row.get(c, '')
                            sheet.write(e_row, i, val, tot_f if 'Total' in c else fmt)

                sheet.freeze_panes(1, 9)

            clean_name = "".join(x for x in target_program if x.isalnum() or x in " -_")
            st.download_button("📥 Download", output.getvalue(), f"Fluxx_Report_FY{target_fy}_{clean_name}.xlsx")
    except FileNotFoundError:
        st.info("No local data found. Please complete Step 1 to sync data.")
    except Exception as e:
        # This will show you the EXACT error (e.g., a Key Error or a Math Error)
        st.error(f"An error occurred during report generation: {e}")
        st.write("Check if the Program and Fiscal Year you selected actually have data in Fluxx.")
