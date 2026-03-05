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
    client_site = st.text_input("Client Site URL", value='https://masscec.fluxx.io')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    st.divider()
    st.caption("Standardized Reporting Engine v8.0")

# --- CORE UTILITIES ---
def get_auth_header():
    try:
        url = f"{client_site.rstrip('/')}/oauth/token"
        data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
        res = requests.post(url, data=data)
        return {'Authorization': f"Bearer {res.json().get('access_token')}"} if res.status_code == 200 else None
    except: return None

def get_all_records(model, cols, headers, relations=None):
    all_recs, page, base_url = [], 1, f"{client_site.rstrip('/')}/api/rest/v2"
    params = {'per_page': 500, 'cols': str(cols).replace("'", '"')}
    if relations: params['relation'] = json.dumps(relations)
    status_msg = st.empty()
    while True:
        params['page'] = page
        res = requests.get(f"{base_url}/{model}", headers=headers, params=params)
        data = res.json()
        records = data.get('records', [])
        if isinstance(records, dict): records = records.get(model, [])
        if not records: break
        all_recs.extend(records)
        status_msg.text(f"Syncing {model}... Page {page}")
        if page >= data.get('total_pages', 1): break
        page += 1
    return pd.DataFrame(all_recs)

def clean_data_types(df):
    for col in df.columns:
        if 'id' in col.lower() and 'base' not in col.lower() and 'program_organization' not in col:
            df[col] = df[col].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
    return df

# --- INTERFACE ---
tab1, tab2 = st.tabs(["🚀 Step 1: Sync Data", "📄 Step 2: Generate Report"])

with tab1:
    if st.button("Sync All Tables"):
        headers = get_auth_header()
        if headers:
            with st.status("Syncing...") as s:
                get_all_records('program', ['id', 'name'], headers).to_csv('raw_program.csv', index=False)
                get_all_records('sub_program', ['id', 'name'], headers).to_csv('raw_sub_program.csv', index=False)
                get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers).to_csv('raw_fsa.csv', index=False)
                get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, relations={"program_organization_id": ["name"]}).to_csv('raw_gr.csv', index=False)
                get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers).to_csv('raw_rfs.csv', index=False)
                get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers).to_csv('raw_p_h.csv', index=False)
                get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers).to_csv('raw_p_s.csv', index=False)
                s.update(label="Complete!", state="complete")

with tab2:
    try:
        prog_opts = sorted(pd.read_csv('raw_program.csv')['name'].unique().tolist())
        target_fy = st.number_input("Fiscal Year", value=2025)
        target_program = st.selectbox("Program", options=prog_opts)
        
        if st.button("Download Report"):
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = clean_data_types(pd.read_csv('raw_gr.csv'))
            df_rfs = clean_data_types(pd.read_csv('raw_rfs.csv')).rename(columns={'id': 'RFS_ID'})
            df_ph = clean_data_types(pd.read_csv('raw_p_h.csv'))
            df_ps = clean_data_types(pd.read_csv('raw_p_s.csv'))
            
            fy_short = str(target_fy)[-2:]
            budget_col = f'Awards Budget Total FY{fy_short}'
            
            # Merging logic
            df_fsa = df_fsa[pd.to_numeric(df_fsa['spending_year']) == target_fy]
            master = df_rfs.merge(df_gr, left_on='request_id', right_on='id', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='id', how='right')
            master = master[master['program_id'].map(pd.read_csv('raw_program.csv').set_index('id')['name']) == target_program]

            # Financial processing
            months = pd.period_range(start=f"{target_fy-1}-07-01", end=f"{target_fy}-06-30", freq='M')
            time_cols = []
            for period in months:
                lbl, q = period.strftime('%b-%y'), (1 if period.month in [7,8,9] else 2 if period.month in [10,11,12] else 3 if period.month in [1,2,3] else 4)
                master[f'{lbl} Awards'] = np.where(pd.to_datetime(master['grant_agreement_at']).dt.to_period('M') == period, master['funding_amount'], 0)
                master[f'{lbl} Payments'] = master['RFS_ID'].map(df_ps.merge(df_ph, left_on='request_transaction_id', right_on='id')[lambda x: pd.to_datetime(x.due_at).dt.to_period('M') == period].groupby('request_funding_source_id')['amount'].sum()).fillna(0)
                time_cols.extend([f'{lbl} Awards', f'{lbl} Payments'])
                if period.month in [9, 12, 3, 6]:
                    master[f'Q{q} FY{fy_short} Awards Total'] = master[[c for c in master.columns if f'Q{q}' not in c and 'Awards' in c and lbl[:3] in c or any(m in c for m in [period.strftime('%b'), (period-1).strftime('%b'), (period-2).strftime('%b')])]].sum(axis=1)
                    time_cols.extend([f'Q{q} FY{fy_short} Awards Total', f'Q{q} FY{fy_short} Payments Total'])

            # Excel Output
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                # Exact original formats
                head_f = workbook.add_format({'bold':True, 'bg_color':'#4472C4', 'font_color':'white', 'border':1, 'align':'center'})
                num_f = workbook.add_format({'num_format':'$#,##0.00', 'border':1})
                tot_f = workbook.add_format({'bold':True, 'bg_color':'#D9E1F2', 'num_format':'$#,##0.00', 'border':1})
                sub_f = workbook.add_format({'bold':True, 'bg_color':'#D3D3D3', 'num_format':'$#,##0.00', 'border':1})
                div_f = workbook.add_format({'bg_color':'#808080', 'border':1})

                info_c = ['Program', 'Sub Focus Area', 'Funding Source', budget_col]
                grant_c = ['Organization Name', 'Request ID', 'Project Title']
                cols = info_c + ['Spacer1'] + grant_c + ['Spacer2'] + time_cols
                
                # Build rows with grouping
                rows = []
                for _, grp in master.groupby('sub_program_id'):
                    for _, f_grp in grp.groupby('funding_source_id'):
                        for i, r in f_grp.iterrows():
                            rows.append({**r, 'Spacer1':"", 'Spacer2':"", 'RowType':'Data'})
                        rows.append({**f_grp[time_cols].sum(), budget_col:f_grp[budget_col].max(), 'Funding Source':'TOTAL', 'RowType':'Sub'})
                
                res_df = pd.DataFrame(rows)
                res_df[cols].to_excel(writer, sheet_name='Report', index=False, startrow=1, header=False)
                sheet = writer.sheets['Report']

                # Headers & Formatting
                for i, c in enumerate(cols):
                    sheet.write(0, i, "" if "Spacer" in c else c, head_f)
                    sheet.set_column(i, i, 2 if "Spacer" in i else 18)

                for r_idx, row in enumerate(rows):
                    fmt = sub_f if row['RowType'] == 'Sub' else num_f
                    for i, c in enumerate(cols):
                        if "Spacer" not in c:
                            val = row.get(c, 0) if any(x in c for x in ['Awards', 'Payments', 'Budget']) else row.get(c, "")
                            sheet.write(r_idx+1, i, val, tot_f if "Total" in c else fmt)

                # ADD DIVIDERS LAST TO PREVENT OVERWRITE
                sheet.set_column(4, 4, 2, div_f) # Spacer 1 (Column E)
                sheet.set_column(8, 8, 2, div_f) # Spacer 2 (Column I)
                
                # Freeze Panes (Up to Column I / Index 8)
                sheet.freeze_panes(1, 9)

            st.download_button("📥 Download", output.getvalue(), f"{target_program}_FY{target_fy}.xlsx")
    except: st.info("Run Step 1 first.")
