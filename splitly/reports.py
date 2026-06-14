import io
import pandas as pd
from django.http import HttpResponse, FileResponse
from django.utils import timezone
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def generate_expenses_csv(expenses):
    """
    Generates a CSV export of expenses using Pandas.
    """
    data = []
    for exp in expenses:
        data.append({
            'Expense ID': exp.id,
            'Title': exp.title,
            'Amount': float(exp.amount),
            'Currency': exp.currency,
            'Date': exp.date.strftime('%Y-%m-%d') if hasattr(exp.date, 'strftime') else str(exp.date),
            'Category': exp.category,
            'Paid By': exp.paid_by.email,
            'Split Type': exp.get_split_type_display(),
            'Location': exp.location or '',
            'Description': exp.description or '',
            'Created By': exp.created_by.email,
            'Created At': exp.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })
        
    if not data:
        df = pd.DataFrame(columns=[
            'Expense ID', 'Title', 'Amount', 'Currency', 'Date', 
            'Category', 'Paid By', 'Split Type', 'Location', 
            'Description', 'Created By', 'Created At'
        ])
    else:
        df = pd.DataFrame(data)
        
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="splitly_expenses_export.csv"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def generate_group_pdf_report(group, cycle, balances, transactions, import_sessions, expenses, settlements_list):
    """
    Generates a professional, comprehensive group PDF report containing members,
    detailed expenses, settlements, CSV import summary, and final balances.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter, 
        rightMargin=36, 
        leftMargin=36, 
        topMargin=36, 
        bottomMargin=36
    )
    story = []
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#4f46e5'),
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#6b7280'),
        spaceAfter=15
    )
    
    h2_style = ParagraphStyle(
        'DocH2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#1f2937'),
        spaceBefore=12,
        spaceAfter=8
    )
    
    normal_style = styles['Normal']
    bold_style = ParagraphStyle('BoldText', parent=normal_style, fontName='Helvetica-Bold')
    
    # Header
    story.append(Paragraph("Splitly - Group Expense Report", title_style))
    story.append(Paragraph(f"Group: {group.name} | Cycle ID: {cycle.id} | Generated: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} UTC", subtitle_style))
    story.append(Spacer(1, 10))
    
    # Description
    if group.description:
        story.append(Paragraph(f"<b>Description:</b> {group.description}", normal_style))
        story.append(Spacer(1, 10))
        
    # Cycle Info
    story.append(Paragraph("Cycle Timeline", h2_style))
    start_str = cycle.start_date.strftime('%Y-%m-%d %H:%M')
    cycle_text = f"Cycle started: {start_str} UTC"
    if cycle.end_date:
        end_str = cycle.end_date.strftime('%Y-%m-%d %H:%M')
        cycle_text += f" | Closed: {end_str} UTC"
    else:
        cycle_text += " | Status: Active"
    story.append(Paragraph(cycle_text, normal_style))
    story.append(Spacer(1, 15))
    
    # Group Members
    story.append(Paragraph("Group Members", h2_style))
    members_data = [["Name", "Email", "Status", "Joined"]]
    for membership in group.memberships.all():
        name = membership.user.get_full_name() or membership.user.username
        members_data.append([
            name, 
            membership.user.email, 
            membership.get_status_display(), 
            membership.join_date.strftime('%Y-%m-%d')
        ])
    members_table = Table(members_data, colWidths=[130, 190, 100, 120])
    members_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    story.append(members_table)
    story.append(Spacer(1, 15))
    
    # Expenses List
    story.append(Paragraph("Expenses Recorded", h2_style))
    if not expenses:
        story.append(Paragraph("No expenses recorded in this cycle.", normal_style))
    else:
        exp_data = [["Date", "Title", "Category", "Paid By", "Amount", "Value (INR)"]]
        total_cycle_inr = 0
        for exp in expenses:
            total_cycle_inr += exp.converted_inr_value
            exp_data.append([
                exp.date.strftime('%Y-%m-%d'),
                exp.title,
                exp.category,
                exp.paid_by.get_full_name() or exp.paid_by.email,
                f"{exp.currency} {exp.amount:.2f}",
                f"₹{exp.converted_inr_value:.2f}"
            ])
        exp_data.append(["", "", "", "Total Spend:", "", f"₹{total_cycle_inr:.2f}"])
        exp_table = Table(exp_data, colWidths=[80, 120, 80, 120, 70, 70])
        exp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(exp_table)
    story.append(Spacer(1, 15))

    # Settlements List
    story.append(Paragraph("Settlements Recorded", h2_style))
    if not settlements_list:
        story.append(Paragraph("No settlements recorded in this cycle.", normal_style))
    else:
        set_data = [["Date", "Payer (Paid From)", "Receiver (Paid To)", "Amount", "Value (INR)"]]
        for s in settlements_list:
            set_data.append([
                s.date.strftime('%Y-%m-%d'),
                s.payer.get_full_name() or s.payer.email,
                s.receiver.get_full_name() or s.receiver.email,
                f"{s.currency} {s.amount:.2f}",
                f"₹{s.converted_inr_value:.2f}"
            ])
        set_table = Table(set_data, colWidths=[90, 150, 150, 80, 70])
        set_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(set_table)
    story.append(Spacer(1, 15))

    # CSV Imports List
    story.append(Paragraph("CSV Imports Summary", h2_style))
    if not import_sessions:
        story.append(Paragraph("No CSV imports performed in this group.", normal_style))
    else:
        imp_data = [["Date", "File Name", "Uploaded By", "Status", "Imported Rows"]]
        for imp in import_sessions:
            imp_data.append([
                imp.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                imp.file_name,
                imp.uploaded_by.email,
                imp.get_status_display(),
                str(imp.imported_rows)
            ])
        imp_table = Table(imp_data, colWidths=[110, 130, 130, 90, 80])
        imp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(imp_table)
    story.append(Spacer(1, 15))
    
    # Balances Table
    story.append(Paragraph("Final Balances (In INR)", h2_style))
    balance_data = [["Member", "Email", "Status", "Net Balance (INR)"]]
    
    for user, bal in balances.items():
        status = "Is Owed" if bal > 0 else ("Owes" if bal < 0 else "Settled")
        formatted_bal = f"{'+' if bal > 0 else ''}₹{bal:.2f}"
        name = user.get_full_name() or user.username
        balance_data.append([name, user.email, status, formatted_bal])
        
    balance_table = Table(balance_data, colWidths=[140, 190, 100, 110])
    balance_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    story.append(balance_table)
    story.append(Spacer(1, 20))
    
    # Suggested Settlements
    story.append(Paragraph("Suggested Transactions to Settle Up", h2_style))
    if not transactions:
        story.append(Paragraph("All members are settled up! No transactions needed.", normal_style))
    else:
        tx_data = [["Debtor (Pays)", "Creditor (Receives)", "Amount (INR)"]]
        for tx in transactions:
            tx_data.append([
                tx['from_user'].get_full_name() or tx['from_user'].email, 
                tx['to_user'].get_full_name() or tx['to_user'].email, 
                f"₹{tx['amount']:.2f}"
            ])
            
        tx_table = Table(tx_data, colWidths=[220, 220, 100])
        tx_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        story.append(tx_table)
        
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_individual_pdf_report(user, memberships, expenses_involved, payments, settlements, categories_summary, overall_stats):
    """
    Generates a high-quality individual expense report for a user, containing:
    - User Details & Profile Overview
    - Membership Timeline
    - Expense History & Contributions
    - Payments Made
    - Settlements Log
    - Category-wise Spending Breakdown
    - Final Balance Breakdown
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter, 
        rightMargin=36, 
        leftMargin=36, 
        topMargin=36, 
        bottomMargin=36
    )
    story = []
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#4f46e5'),
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#6b7280'),
        spaceAfter=15
    )
    
    h2_style = ParagraphStyle(
        'DocH2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#1f2937'),
        spaceBefore=12,
        spaceAfter=8
    )
    
    normal_style = styles['Normal']
    
    # Header
    story.append(Paragraph(f"Splitly - Individual Financial Report", title_style))
    story.append(Paragraph(f"Generated for: {user.get_full_name() or user.email} | Date: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} UTC", subtitle_style))
    story.append(Spacer(1, 10))
    
    # User Details
    story.append(Paragraph("User Profile Summary", h2_style))
    details_data = [
        ["Field", "Value"],
        ["Full Name", user.get_full_name() or "N/A"],
        ["Email Address", user.email],
        ["Phone Number", user.phone_number or "N/A"],
        ["Preferred Currency", user.preferred_currency]
    ]
    details_table = Table(details_data, colWidths=[150, 390])
    details_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    story.append(details_table)
    story.append(Spacer(1, 15))
    
    # Membership Timeline
    story.append(Paragraph("Membership Timeline", h2_style))
    if not memberships:
        story.append(Paragraph("No active or historical group memberships found.", normal_style))
    else:
        memb_data = [["Group Name", "Join Date", "Leave Date", "Status"]]
        for m in memberships:
            memb_data.append([
                m.group.name,
                m.join_date.strftime('%Y-%m-%d %H:%M'),
                m.leave_date.strftime('%Y-%m-%d %H:%M') if m.leave_date else 'Present',
                m.get_status_display()
            ])
        memb_table = Table(memb_data, colWidths=[200, 120, 120, 100])
        memb_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        story.append(memb_table)
    story.append(Spacer(1, 15))

    # Final Balance Summary
    story.append(Paragraph("Financial Summary (All Groups)", h2_style))
    summary_data = [
        ["Metric", "Amount in INR"],
        ["Total Amount Paid (Expenses Paid)", f"₹{overall_stats['total_paid']:.2f}"],
        ["Total Amount Shared (User's Debts Owed)", f"₹{overall_stats['total_shared']:.2f}"],
        ["Total Settlements Sent", f"₹{overall_stats['settlements_sent']:.2f}"],
        ["Total Settlements Received", f"₹{overall_stats['settlements_received']:.2f}"],
        ["Net Financial Balance", f"{'+' if overall_stats['net_balance'] > 0 else ''}₹{overall_stats['net_balance']:.2f}"]
    ]
    summary_table = Table(summary_data, colWidths=[250, 290])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#4f46e5') if overall_stats['net_balance'] >= 0 else colors.HexColor('#ef4444')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 15))

    # Category Expenses Breakdown
    story.append(Paragraph("Spending by Category", h2_style))
    if not categories_summary:
        story.append(Paragraph("No category spending information available.", normal_style))
    else:
        cat_data = [["Category", "Spend in INR", "Share (%)"]]
        for k, val in categories_summary.items():
            cat_data.append([k, f"₹{val['amount']:.2f}", f"{val['percentage']:.1f}%"])
        cat_table = Table(cat_data, colWidths=[180, 180, 180])
        cat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        story.append(cat_table)
    story.append(Spacer(1, 15))

    # Payments Made List
    story.append(Paragraph("Expenses Paid By User", h2_style))
    if not payments:
        story.append(Paragraph("You have not recorded payments for any expenses.", normal_style))
    else:
        pay_data = [["Date", "Group", "Expense Title", "Amount Paid", "Value in INR"]]
        for p in payments:
            pay_data.append([
                p.date.strftime('%Y-%m-%d'),
                p.cycle.group.name,
                p.title,
                f"{p.currency} {p.amount:.2f}",
                f"₹{p.converted_inr_value:.2f}"
            ])
        pay_table = Table(pay_data, colWidths=[80, 110, 170, 90, 90])
        pay_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(pay_table)
    story.append(Spacer(1, 15))
    
    # Expense History (Involved but not necessarily paid by them)
    story.append(Paragraph("Expenses User Shared (Debt Items)", h2_style))
    if not expenses_involved:
        story.append(Paragraph("You were not registered as a participant in any split expenses.", normal_style))
    else:
        inv_data = [["Date", "Group", "Expense Title", "Paid By", "Owed Amount", "Value in INR"]]
        for split in expenses_involved:
            inv_data.append([
                split.expense.date.strftime('%Y-%m-%d'),
                split.expense.cycle.group.name,
                split.expense.title,
                split.expense.paid_by.get_full_name() or split.expense.paid_by.email,
                f"{split.expense.currency} {split.amount:.2f}",
                f"₹{split.amount_inr:.2f}"
            ])
        inv_table = Table(inv_data, colWidths=[80, 100, 140, 110, 55, 55])
        inv_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(inv_table)
    story.append(Spacer(1, 15))

    # Settlements Sent/Received
    story.append(Paragraph("Settlements History", h2_style))
    if not settlements:
        story.append(Paragraph("No settlements recorded for your account.", normal_style))
    else:
        set_data = [["Date", "Group", "Type", "Counterparty", "Amount", "Value in INR"]]
        for s in settlements:
            is_payer = s.payer == user
            stype = "Sent" if is_payer else "Received"
            counterparty = s.receiver if is_payer else s.payer
            cname = counterparty.get_full_name() or counterparty.email
            set_data.append([
                s.date.strftime('%Y-%m-%d'),
                s.group.name,
                stype,
                cname,
                f"{s.currency} {s.amount:.2f}",
                f"₹{s.converted_inr_value:.2f}"
            ])
        set_table = Table(set_data, colWidths=[80, 110, 70, 130, 75, 75])
        set_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(set_table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
