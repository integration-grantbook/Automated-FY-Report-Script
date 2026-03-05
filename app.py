import streamlit as st
import requests
import pandas as pd
import numpy as np
import sys
import json
import time
import os
import ast
import io
from datetime import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Fluxx Snapshot Tool", layout="wide")

st.title("📊 Fluxx Executive Snapshot Portal")
st.markdown("Consolidated Data Fetcher and Executive Reporter")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("1. API Configuration")
    client_site = st.text_input("Client Site", value='https://masscec.fluxx.io')
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    
    st.divider()
    
    st.header("2. Report Parameters")
    target_fy = st.number_input("Target Fiscal Year", value=2025)
    target_program = st.text_input("Target Program Filter", value="Offshore Wind")

# --- PART 1: DATA FETCHER FUNCTIONS ---
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
        st.error(f"Auth Error: {e}")
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
        status_msg.text(f"Indexing {model}... Page {page} ({len(all_recs)} records)")
        if page >= data.get('total_pages', 1): break
        page += 1
        time.sleep(0.5)
    return pd.DataFrame(all_recs)

# --- PART 2: CLEANING & PROCESSING FUNCTIONS ---
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
        if isinstance(val, list):
            if len(val) > 0 and isinstance(val[0], dict): return val[0].get('name', 'Unknown')
            return 'Unknown'
        if isinstance(val, str) and val.strip().startswith('['):
            val_list = ast.literal_eval(val)
            if len(val_list) > 0 and isinstance(val_list[0], dict): return val_list[0].get('name', 'Unknown')
    except: pass
    return 'Unknown'

def clean_date(df, col):
    if df.empty: return df
    df[col] = pd.to_datetime(df[col], errors='coerce').dt.tz_localize(None)
    return df

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 Step 1: Data Pull", "📄 Step 2: Generate Snapshot"])

with tab1:
    st.subheader("Fetch Data from Fluxx")
    col1, col2 = st.columns(2)
    with col1:
        PULL_REFERENCES = st.checkbox("Reference Tables", value=True)
        PULL_BUDGETS_FSA = st.checkbox("Budgets (FSA)", value=True)
        PULL_GRANTS_HEADER = st.checkbox("Grant Headers", value=True)
    with col2:
        PULL_RFS_SPLITS = st.checkbox("Grant Splits (RFS)", value=True)
        PULL_AMENDMENTS = st.checkbox("Amendments", value=True)
        PULL_PAYMENTS = st.checkbox("Payments", value=True)

    if st.button("Start Global Pull"):
        if not client_id or not client_secret:
            st.error("Missing Credentials in Sidebar")
        else:
            headers = get_auth_header()
            if headers:
                with st.status("Pulling Data...", expanded=True) as status:
                    if PULL_REFERENCES:
                        st.write("Pulling References...")
                        get_all_records('program', ['id', 'name'], headers).to_csv('raw_program.csv', index=False)
                        get_all_records('sub_program', ['id', 'name'], headers).to_csv('raw_sub_program.csv', index=False)
                        get_all_records('funding_source', ['id', 'name', 'start_at', 'end_at'], headers).to_csv('raw_funding_source.csv', index=False)
                    
                    if PULL_BUDGETS_FSA:
                        st.write("Pulling Budgets...")
                        get_all_records('funding_source_allocation', ['id', 'program_id', 'sub_program_id', 'funding_source_id', 'amount', 'spending_year'], headers).to_csv('raw_fsa.csv', index=False)

                    if PULL_GRANTS_HEADER:
                        st.write("Pulling Grant Headers...")
                        df_gr = get_all_records('grant_request', ['id', 'base_request_id', 'project_title', 'grant_agreement_at', 'program_organization_id'], headers, relations={"program_organization_id": ["name"]})
                        df_gr['Grantee'] = "Unknown"
                        if not df_gr.empty and 'program_organization' in df_gr.columns:
                            df_gr['Grantee'] = df_gr['program_organization'].apply(lambda x: x.get('name') if isinstance(x, dict) else "Unknown")
                            df_gr = df_gr.drop(columns=['program_organization'], errors='ignore')
                        df_gr.to_csv('raw_grant_requests.csv', index=False)

                    if PULL_RFS_SPLITS:
                        st.write("Pulling Grant Splits...")
                        get_all_records('request_funding_source', ['id', 'request_id', 'funding_source_allocation_id', 'funding_amount'], headers).to_csv('raw_split_rfs.csv', index=False)

                    if PULL_AMENDMENTS:
                        st.write("Pulling Amendments...")
                        get_all_records('request_amendment', ['request_id', 'amended_at', 'amount_recommended_difference'], headers).to_csv('raw_amendments.csv', index=False)

                    if PULL_PAYMENTS:
                        st.write("Pulling Payments...")
                        get_all_records('request_transaction', ['id', 'request_id', 'due_at'], headers).to_csv('raw_payments_header.csv', index=False)
                        get_all_records('request_transaction_funding_source', ['id', 'request_transaction_id', 'request_funding_source_id', 'amount'], headers).to_csv('raw_payment_splits.csv', index=False)
                    
                    status.update(label="Pull Complete!", state="complete")
                st.success("Local CSVs Refreshed.")

with tab2:
    st.subheader("Generate Executive Snapshot")
    if st.button("Generate Excel Report"):
        try:
            # 1. LOAD & PREP
            df_prog = clean_data_types(pd.read_csv('raw_program.csv'))
            df_sub = clean_data_types(pd.read_csv('raw_sub_program.csv'))
            df_fund = clean_data_types(pd.read_csv('raw_funding_source.csv'))
            df_fsa = clean_data_types(pd.read_csv('raw_fsa.csv'))
            df_gr = pd.read_csv('raw_grant_requests.csv')
            df_split_rfs = clean_data_types(pd.read_csv('raw_split_rfs.csv'))
            df_amend = clean_data_types(pd.read_csv('raw_amendments.csv'))
            df_pay_head = clean_data_types(pd.read_csv('raw_payments_header.csv'))
            df_pay_split = clean_data_types(pd.read_csv('raw_payment_splits.csv'))

            # Org Names Logic
            if 'program_organization' in df_gr.columns:
                df_gr['Grantee'] = df_gr['program_organization'].apply(extract_org_name)
            elif 'program_organization_id' in df_gr.columns:
                df_gr['Grantee'] = df_gr['program_organization_id'].apply(extract_org_name)
            else:
                df_gr['Grantee'] = "Unknown"
            df_gr = clean_data_types(df_gr)

            # Dates Configuration
            fy_short = str(target_fy)[-2:]
            fy_start_date = f"{target_fy - 1}-07-01"
            fy_end_date = f"{target_fy}-06-30"
            fy_start = pd.to_datetime(fy_start_date)
            fy_end = pd.to_datetime(fy_end_date)
            months = pd.period_range(start=fy_start, end=fy_end, freq='M')

            # Funding Source Dates
            df_fund['start_at'] = pd.to_datetime(df_fund.get('start_at'), errors='coerce')
            df_fund['end_at'] = pd.to_datetime(df_fund.get('end_at'), errors='coerce')
            df_fund['s_str'] = df_fund['start_at'].apply(lambda x: x.strftime('%m/%d/%Y') if pd.notnull(x) else '')
            df_fund['e_str'] = df_fund['end_at'].apply(lambda x: x.strftime('%m/%d/%Y') if pd.notnull(x) else '')
            df_fund['Funding Source Dates'] = (df_fund['s_str'] + " - " + df_fund['e_str']).replace(' - ', '').replace(' -', '').replace('- ', '')

            # Build Budgets
            df_prog = df_prog.rename(columns={'id': 'p_id', 'name': 'Program'})
            df_sub = df_sub.rename(columns={'id': 'sp_id', 'name': 'Sub Focus Area'})
            df_fund = df_fund.rename(columns={'id': 'fs_id', 'name': 'Funding Source'})

            df_fsa['spending_year'] = pd.to_numeric(df_fsa['spending_year'], errors='coerce').fillna(0).astype(int)
            df_fsa = df_fsa[df_fsa['spending_year'] == target_fy].copy()
            
            df_fsa = df_fsa.merge(df_prog, left_on='program_id', right_on='p_id', how='left')
            df_fsa = df_fsa.merge(df_sub, left_on='sub_program_id', right_on='sp_id', how='left')
            df_fsa = df_fsa.merge(df_fund, left_on='funding_source_id', right_on='fs_id', how='left')

            budget_col_name = f'Awards Budget Total FY{fy_short}'
            df_fsa = df_fsa.rename(columns={'amount': budget_col_name, 'id': 'FSA_ID'})
            df_fsa[f'Adjustments to FY{fy_short} Budget'] = np.nan
            df_fsa[f'Net FY{fy_short} Budget'] = np.nan
            df_fsa = df_fsa[['FSA_ID', 'Program', 'Sub Focus Area', 'Funding Source', 'Funding Source Dates', budget_col_name, f'Adjustments to FY{fy_short} Budget', f'Net FY{fy_short} Budget']]

            # Prepare Rows
            df_split_rfs = df_split_rfs.rename(columns={'funding_amount': 'RFS_Amount', 'id': 'RFS ID'}) if 'funding_amount' in df_split_rfs.columns else df_split_rfs.rename(columns={'amount': 'RFS_Amount', 'id': 'RFS ID'})
            df_gr = df_gr.rename(columns={'id': 'Request_ID', 'base_request_id': 'Request ID'})
            master = df_split_rfs.merge(df_gr, left_on='request_id', right_on='Request_ID', how='left')
            master = master.merge(df_fsa, left_on='funding_source_allocation_id', right_on='FSA_ID', how='right')
            master = master.rename(columns={'Grantee': 'Organization Name', 'project_title': 'Project Title'})

            # Financials
            master = clean_date(master, 'grant_agreement_at')
            df_pay_head = clean_date(df_pay_head, 'due_at')
            df_pay_full = df_pay_split.merge(df_pay_head, left_on='request_transaction_id', right_on='id', how='left')

            for q in [1, 2, 3, 4]:
                master[f'Q{q} FY{fy_short} Awards Total'] = 0.0
                master[f'Q{q} FY{fy_short} Payments Total'] = 0.0
            
            master[f'Total Awards FY{fy_short}'] = 0.0
            master[f'Total Payments FY{fy_short}'] = 0.0
            ordered_cols, red_cols, border_cols, source_map = [], [], [], {}

            for period in months:
                month_lbl = period.strftime('%b-%y')
                # Awards
                col_award = f'{month_lbl} Awards'
                master[col_award] = np.where(master['grant_agreement_at'].dt.to_period('M') == period, master['RFS_Amount'], 0.0)
                ordered_cols.append(col_award)
                source_map[col_award] = "Request Funding Source -> Amount (Slotted by GrantRequest.GrantAgreementAt)"

                # Amendments (Placeholders)
                cols_amend = [f'{month_lbl} Positive Amendments', f'{month_lbl} Total Award Increases', f'{month_lbl} Negative Amendments']
                for c in cols_amend: 
                    master[c] = np.nan
                    red_cols.append(c)
                    source_map[c] = "Placeholder"
                ordered_cols.extend(cols_amend)

                # Payments
                valid_pays = df_pay_full[(df_pay_full['amount'] > 0) & (df_pay_full['due_at'].dt.to_period('M') == period)]
                pays_grouped = valid_pays.groupby('request_funding_source_id')['amount'].sum()
                col_pay = f'{month_lbl} Payments'
                master[col_pay] = master['RFS ID'].map(pays_grouped).fillna(0)
                ordered_cols.append(col_pay)
                source_map[col_pay] = "RequestTransactionFundingSource.Amount (Slotted by RequestTransaction.DueDate)"

                # Quarters Logic
                m_num = period.month
                q = 1 if m_num in [7,8,9] else 2 if m_num in [10,11,12] else 3 if m_num in [1,2,3] else 4
                master[f'Q{q} FY{fy_short} Awards Total'] += master[col_award]
                master[f'Q{q} FY{fy_short} Payments Total'] += master[col_pay]
                master[f'Total Awards FY{fy_short}'] += master[col_award]
                master[f'Total Payments FY{fy_short}'] += master[col_pay]

                if m_num in [9, 12, 3, 6]:
                    q_award_col = f'Q{q} FY{fy_short} Awards Total'
                    ordered_cols.append(q_award_col)
                    source_map[q_award_col] = f"Sum of Quarter Awards"
                    
                    q_amend_cols = [f'Q{q} FY{fy_short} Positive Amendments', f'Q{q} FY{fy_short} Total Award Increases', f'Q{q} FY{fy_short} Negative Amendments']
                    for c in q_amend_cols: 
                        master[c] = np.nan
                        red_cols.append(c)
                        source_map[c] = "Placeholder"
                    ordered_cols.extend(q_amend_cols)
                    
                    q_pay_col = f'Q{q} FY{fy_short} Payments Total'
                    ordered_cols.append(q_pay_col)
                    source_map[q_pay_col] = f"Sum of Quarter Payments"
                    border_cols.append(q_pay_col)

            # Annual Totals
            ann_cols = [f'Total Awards FY{fy_short}', f'Total Positive Amendments FY{fy_short}', f'Total Award Increases FY{fy_short}', f'Total Negative Amendments FY{fy_short}', f'Total Payments FY{fy_short}']
            for c in ann_cols:
                if 'Amendments' in c or 'Increases' in c: master[c] = np.nan
                ordered_cols.append(c)
                source_map[c] = "Annual Calculation"
                if 'Amendments' in c or 'Increases' in c: red_cols.append(c)
            border_cols.append(f'Total Payments FY{fy_short}')

            final_calcs = ['Remaining Contract balance to be paid', 'Award Budget less actual Award increases $ Difference', '% Difference']
            for c in final_calcs: 
                master[c] = np.nan
                ordered_cols.append(c)
                red_cols.append(c)
                source_map[c] = "Placeholder"

            static_map = {
                'Program': 'Funding Source Allocation -> Program -> Name', 'Sub Focus Area': 'Funding Source Allocation -> SubProgram -> Name',
                'Funding Source': 'Funding Source Allocation -> FundingSource -> Name', 'Funding Source Dates': 'Funding Source Allocation -> FundingSource -> Start/End Date',
                budget_col_name: 'Funding Source Allocation -> Amount', f'Adjustments to FY{fy_short} Budget': 'Placeholder',
                f'Net FY{fy_short} Budget': 'Placeholder', 'RFS ID': 'Request Funding Source -> ID', 'Organization Name': 'Request Funding Source -> Grant Request -> Organization -> Name',
                'Request ID': 'Request Funding Source -> Grant Request -> BaseRequestId', 'Project Title': 'Request Funding Source -> Grant Request -> ProjectTitle'
            }
            source_map.update(static_map)
            red_cols.extend([f'Adjustments to FY{fy_short} Budget', f'Net FY{fy_short} Budget'])

            # Excel Generation Setup
            logic_data = {
                'Step': [1, 2, 3, 4, 5],
                'Component': ['Budgets (Left Side)', 'Rows (Grants)', 'Columns (Time)', 'Values (Awards)', 'Values (Payments)'],
                'Description': [
                    'The report is anchored on "Funding Source Allocations" (Budgets) for the selected FY.',
                    'Each row represents a "Request Funding Source" record to handle split grants.',
                    'Columns are generated dynamically from July to June.',
                    'Award amounts come from RFS amount based on Grant Approval Date.',
                    'Payment amounts come from RequestTransactionFundingSource based on Due Date.'
                ]
            }
            df_logic = pd.DataFrame(logic_data)

            output = io.BytesIO()
            if target_program and target_program != "None": master = master[master['Program'] == target_program]
            unique_programs = master['Program'].dropna().unique() if len(master['Program'].dropna().unique()) > 0 else ['No_Data']

            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                # Formatting Styles
                header_fmt = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
                red_header_fmt = workbook.add_format({'bold': True, 'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
                source_fmt = workbook.add_format({'italic': True, 'font_color': '#595959', 'bg_color': '#F2F2F2', 'border': 1, 'font_size': 9, 'text_wrap': True, 'valign': 'top'})
                divider_fmt = workbook.add_format({'bg_color': '#808080'})
                subtotal_num_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'num_format': '$#,##0.00'})
                subtotal_num_border_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'num_format': '$#,##0.00', 'right': 1})
                subtotal_txt_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3'})
                sub_prog_fmt = workbook.add_format({'bold': True, 'bg_color': '#ACB9CA', 'num_format': '$#,##0.00'})
                sub_prog_border_fmt = workbook.add_format({'bold': True, 'bg_color': '#ACB9CA', 'num_format': '$#,##0.00', 'right': 1})
                grand_fmt = workbook.add_format({'bold': True, 'bg_color': '#8EA9DB', 'num_format': '$#,##0.00'})
                grand_border_fmt = workbook.add_format({'bold': True, 'bg_color': '#8EA9DB', 'num_format': '$#,##0.00', 'right': 1})
                money_fmt = workbook.add_format({'num_format': '$#,##0.00'})
                money_border_fmt = workbook.add_format({'num_format': '$#,##0.00', 'right': 1})

                for prog_name in unique_programs:
                    sheet_name = str(prog_name)[:30].replace('/', '-')
                    df_p = master[master['Program'] == prog_name].copy()
                    df_p = df_p[(df_p['RFS ID'].notna()) | (df_p[budget_col_name] > 0)]
                    df_p = df_p[df_p['Funding Source'].notna() & (df_p['Sub Focus Area'].notna())]
                    df_p['FSA_Key'] = df_p['Funding Source'].astype(str) + "|" + df_p['FSA_ID'].astype(str)

                    info_cols = ['Program', 'Sub Focus Area', 'Funding Source', 'Funding Source Dates', budget_col_name, f'Adjustments to FY{fy_short} Budget', f'Net FY{fy_short} Budget']
                    grant_cols = ['RFS ID', 'Organization Name', 'Request ID', 'Project Title']
                    all_cols_ordered = info_cols + ['DIV1'] + grant_cols + ['DIV2'] + ordered_cols

                    final_rows, grand_totals, grand_budget = [], {c: 0.0 for c in ordered_cols}, 0.0

                    for sub_prog, sub_group in df_p.groupby('Sub Focus Area'):
                        sub_totals, sub_budget = {c: 0.0 for c in ordered_cols}, 0.0
                        for fsa_key, group in sub_group.sort_values(by=['Funding Source', 'Organization Name']).groupby('FSA_Key', sort=False):
                            fs_totals, fs_budget = group[ordered_cols].sum(), group[budget_col_name].max()
                            sub_budget += fs_budget
                            for c in ordered_cols: sub_totals[c] += fs_totals[c]

                            first_row = True
                            for idx, row in group.iterrows():
                                row_data = row.to_dict()
                                row_data.update({'DIV1': "", 'DIV2': ""})
                                if pd.isna(row_data.get('RFS ID')):
                                    row_data['Row_Type'] = 'Budget_Only'
                                    for c in grant_cols + ordered_cols: 
                                        if pd.isna(row_data[c]) or row_data[c] == 0: row_data[c] = np.nan
                                else:
                                    row_data['Row_Type'] = 'Data'
                                    for nc in ordered_cols: 
                                        if row_data[nc] == 0: row_data[nc] = np.nan
                                if not first_row:
                                    for c in info_cols: row_data[c] = ""
                                final_rows.append(row_data)
                                first_row = False

                            fs_row = fs_totals.to_dict()
                            fs_row.update({'DIV1': "", 'DIV2': "", 'Program': "", 'Sub Focus Area': "", 'Funding Source': f"TOTAL: {group['Funding Source'].iloc[0]}", 'Funding Source Dates': "", budget_col_name: fs_budget, 'Row_Type': 'FS_Subtotal'})
                            final_rows.append(fs_row)

                        sp_row = sub_totals.copy()
                        sp_row.update({'DIV1': "", 'DIV2': "", 'Program': "", 'Sub Focus Area': f"TOTAL: {sub_prog.upper()}", budget_col_name: sub_budget, 'Row_Type': 'SP_Total'})
                        grand_budget += sub_budget
                        for c in ordered_cols: grand_totals[c] += sub_totals[c]
                        final_rows.append(sp_row)
                        final_rows.append({c: "" for c in all_cols_ordered})

                    gt_row = grand_totals.copy()
                    gt_row.update({'DIV1': "", 'DIV2': "", 'Program': f"GRAND TOTAL: {prog_name.upper()}", budget_col_name: grand_budget, 'Row_Type': 'Grand_Total'})
                    final_rows.append(gt_row)

                    sheet_df = pd.DataFrame(final_rows)
                    save_df = sheet_df.drop(columns=['Row_Type'], errors='ignore')[all_cols_ordered]
                    save_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=2, header=False)
                    worksheet = writer.sheets[sheet_name]

                    # Formatting Logic
                    for idx, col in enumerate(save_df.columns):
                        if col in ['DIV1', 'DIV2']: worksheet.set_column(idx, idx, 1, divider_fmt)
                        else: worksheet.set_column(idx, idx, 15)
                    
                    for col_num, value in enumerate(save_df.columns.values):
                        if value in ['DIV1', 'DIV2']:
                            worksheet.write(0, col_num, "", divider_fmt); worksheet.write(1, col_num, "", divider_fmt)
                        else:
                            worksheet.write(0, col_num, value, red_header_fmt if value in red_cols else header_fmt)
                            worksheet.write(1, col_num, source_map.get(value, "Calculated"), source_fmt)

                    for r_idx, r_data in enumerate(final_rows):
                        e_row, r_type = r_idx + 2, r_data.get('Row_Type')
                        if r_type in ['FS_Subtotal', 'SP_Total', 'Grand_Total']:
                            style = (subtotal_txt_fmt, subtotal_num_fmt, subtotal_num_border_fmt) if r_type == 'FS_Subtotal' else (sub_prog_fmt, sub_prog_fmt, sub_prog_border_fmt) if r_type == 'SP_Total' else (grand_fmt, grand_fmt, grand_border_fmt)
                            worksheet.set_row(e_row, None, style[0])
                            worksheet.write(e_row, 4, r_data.get(budget_col_name), style[1])
                            for i, c_name in enumerate(ordered_cols):
                                val = r_data.get(c_name)
                                if pd.notna(val) and val != 0: worksheet.write(e_row, len(info_cols) + 1 + len(grant_cols) + 1 + i, val, style[2] if c_name in border_cols else style[1])
                        worksheet.write(e_row, len(info_cols), "", divider_fmt)
                        worksheet.write(e_row, len(info_cols) + 1 + len(grant_cols), "", divider_fmt)

                df_logic.to_excel(writer, index=False, sheet_name='Report Logic')
            
            st.success("Report Generated!")
            st.download_button(label="📥 Download Excel Snapshot", data=output.getvalue(), file_name=f"Fluxx_Snapshot_FY{target_fy}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error(f"Processing Error: {e}")
