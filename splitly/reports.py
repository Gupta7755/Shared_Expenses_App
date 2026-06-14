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
        # Create empty DataFrame with correct column headers
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


def generate_group_pdf_report(group, cycle, balances, transactions):
    """
    Generates a professional PDF report containing group info, 
    member balances, and simplified debt transactions using ReportLab.
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
    
    # Custom styles
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
    
    # Title & Header info
    story.append(Paragraph("Splitly - Group Expense Report", title_style))
    story.append(Paragraph(f"Group: {group.name} | Report Generated: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} UTC", subtitle_style))
    story.append(Spacer(1, 10))
    
    # Description
    if group.description:
        story.append(Paragraph(f"<b>Group Description:</b> {group.description}", normal_style))
        story.append(Spacer(1, 10))
        
    # Cycle Info
    story.append(Paragraph("Current Expense Cycle Info", h2_style))
    start_str = cycle.start_date.strftime('%Y-%m-%d %H:%M')
    cycle_text = f"Cycle started: {start_str} UTC"
    if cycle.end_date:
        end_str = cycle.end_date.strftime('%Y-%m-%d %H:%M')
        cycle_text += f" | Closed: {end_str} UTC"
    else:
        cycle_text += " | Status: Active"
        
    story.append(Paragraph(cycle_text, normal_style))
    story.append(Spacer(1, 15))
    
    # Balances Table
    story.append(Paragraph("Member Balances (Within Cycle)", h2_style))
    balance_data = [["Member Name", "Email", "Status", "Net Balance"]]
    
    for user, bal in balances.items():
        status = "Is Owed" if bal > 0 else ("Owes" if bal < 0 else "Settled")
        formatted_bal = f"{'+' if bal > 0 else ''}{bal:.2f}"
        name = user.get_full_name() or user.username
        balance_data.append([name, user.email, status, formatted_bal])
        
    balance_table = Table(balance_data, colWidths=[150, 180, 100, 110])
    balance_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
    ]))
    story.append(balance_table)
    story.append(Spacer(1, 20))
    
    # Suggested Settlements
    story.append(Paragraph("Suggested Transactions to Settle Up", h2_style))
    if not transactions:
        story.append(Paragraph("All members are settled up! No transactions needed.", normal_style))
    else:
        tx_data = [["Debtor (Pays)", "Creditor (Receives)", "Amount"]]
        for tx in transactions:
            tx_data.append([
                tx['from_user'].get_full_name() or tx['from_user'].email, 
                tx['to_user'].get_full_name() or tx['to_user'].email, 
                f"{tx['amount']:.2f}"
            ])
            
        tx_table = Table(tx_data, colWidths=[220, 220, 100])
        tx_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
        ]))
        story.append(tx_table)
        
    doc.build(story)
    buffer.seek(0)
    
    filename = f"splitly_report_{group.name.replace(' ', '_')}_{timezone.now().strftime('%Y%m%d')}.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename)
