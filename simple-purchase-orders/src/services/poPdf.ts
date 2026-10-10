import PDFDocument from 'pdfkit';

export interface PoPdfLine {
  sku: string;
  productTitle?: string | null;
  variantTitle?: string | null;
  qtyOrdered: number;
  qtyReceived: number;
  unitCost?: number | string | null;
}

export interface PoPdfData {
  poNumber: string;
  status: string;
  createdAt: Date;
  expectedAt?: Date | null;
  notes?: string | null;
  supplier: {
    name: string;
    contactName?: string | null;
    email?: string | null;
    phone?: string | null;
  };
  locationName?: string | null;
  shopName?: string | null;
  lines: PoPdfLine[];
}

function money(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—';
  return `$${Number(v).toFixed(2)}`;
}

function fmtDate(d: Date | null | undefined): string {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/** Render a purchase order as a PDF Buffer (used for supplier emails + download). */
export function renderPurchaseOrderPdf(data: PoPdfData): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ margin: 50, size: 'A4' });
    const chunks: Buffer[] = [];
    doc.on('data', (c: Buffer) => chunks.push(c));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    // Header
    doc.fontSize(22).text('PURCHASE ORDER');
    doc.moveDown(0.25);
    doc.fontSize(12).text(data.poNumber);
    doc.fontSize(10).fillColor('#555');
    doc.text(`Status: ${data.status}    Created: ${fmtDate(data.createdAt)}    Expected: ${fmtDate(data.expectedAt)}`);
    if (data.shopName) doc.text(`From: ${data.shopName}`);
    doc.fillColor('#000');
    doc.moveDown(0.75);

    // Supplier block
    doc.fontSize(12).text('Supplier', { underline: true });
    doc.fontSize(10);
    doc.text(data.supplier.name);
    if (data.supplier.contactName) doc.text(`Attn: ${data.supplier.contactName}`);
    if (data.supplier.email) doc.text(data.supplier.email);
    if (data.supplier.phone) doc.text(data.supplier.phone);
    if (data.locationName) {
      doc.moveDown(0.5);
      doc.fontSize(12).text('Deliver to', { underline: true });
      doc.fontSize(10).text(data.locationName);
    }
    doc.moveDown(0.75);

    // Lines table
    const cols = [
      { label: 'SKU', width: 110 },
      { label: 'Item', width: 210 },
      { label: 'Qty', width: 50, align: 'right' as const },
      { label: 'Unit cost', width: 70, align: 'right' as const },
      { label: 'Total', width: 70, align: 'right' as const },
    ];
    const startX = doc.x;
    let y = doc.y;
    doc.fontSize(10).fillColor('#555');
    let x = startX;
    for (const c of cols) {
      doc.text(c.label, x, y, { width: c.width, align: c.align ?? 'left' });
      x += c.width;
    }
    doc.fillColor('#000');
    y += 16;
    doc.moveTo(startX, y).lineTo(startX + 510, y).strokeColor('#ccc').stroke();
    y += 6;

    let grandTotal = 0;
    doc.fontSize(10);
    for (const line of data.lines) {
      const itemLabel = [line.productTitle, line.variantTitle].filter(Boolean).join(' — ');
      const unit = line.unitCost === null || line.unitCost === undefined || line.unitCost === ''
        ? null
        : Number(line.unitCost);
      const total = unit === null ? null : unit * line.qtyOrdered;
      if (total !== null) grandTotal += total;

      x = startX;
      const rowTop = y;
      doc.text(line.sku, x, y, { width: cols[0].width }); x += cols[0].width;
      doc.text(itemLabel || '—', x, y, { width: cols[1].width }); x += cols[1].width;
      doc.text(String(line.qtyOrdered), x, y, { width: cols[2].width, align: 'right' }); x += cols[2].width;
      doc.text(money(unit), x, y, { width: cols[3].width, align: 'right' }); x += cols[3].width;
      doc.text(total === null ? '—' : money(total), x, y, { width: cols[4].width, align: 'right' });
      y = Math.max(doc.y, rowTop + 16) + 4;
      if (y > 720) {
        doc.addPage();
        y = 70;
      }
    }

    doc.moveTo(startX, y).lineTo(startX + 510, y).strokeColor('#ccc').stroke();
    y += 8;
    doc.fontSize(11).text(`Total: ${money(grandTotal)}`, startX, y, { align: 'right', width: 510 });

    if (data.notes) {
      doc.moveDown(1.5);
      doc.fontSize(12).text('Notes', { underline: true });
      doc.fontSize(10).text(data.notes);
    }

    doc.end();
  });
}
