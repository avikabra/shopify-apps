import cron from 'node-cron';
import { prisma } from '../db.js';
import { sendLowStockDigest } from '../services/alerts.js';

/**
 * Hourly scheduler. Each shop picks a UTC hour for its digest; this job runs
 * once an hour and sends to shops whose hour matches.
 */
export function startDigestScheduler() {
  cron.schedule('7 * * * *', async () => {
    const hour = new Date().getUTCHours();
    const shops = await prisma.shop.findMany({
      where: { digestEnabled: true, digestHourUtc: hour, uninstalledAt: null },
      select: { id: true },
    });
    for (const s of shops) {
      try {
        const result = await sendLowStockDigest(s.id);
        console.log(`[digest] shop ${s.id}: ${result.sent ? 'sent' : 'skipped'} (${result.reason})`);
      } catch (err) {
        console.error(`[digest] shop ${s.id} failed:`, err);
      }
    }
  });
  console.log('[digest] scheduler started (hourly)');
}
