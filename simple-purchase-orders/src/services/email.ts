import nodemailer from 'nodemailer';
import { config } from '../config.js';

export interface EmailAttachment {
  filename: string;
  content: Buffer;
  contentType: string;
}

export interface SendEmailArgs {
  to: string;
  subject: string;
  html: string;
  text?: string;
  attachments?: EmailAttachment[];
}

export interface SendEmailResult {
  /** false when the email was only logged (EMAIL_MODE=log or SMTP not configured). */
  sent: boolean;
  messageId?: string;
}

function smtpConfigured(): boolean {
  return config.emailMode === 'smtp' && Boolean(config.smtpHost);
}

const transporter = smtpConfigured()
  ? nodemailer.createTransport({
      host: config.smtpHost,
      port: config.smtpPort,
      secure: config.smtpPort === 465,
      auth:
        config.smtpUser && config.smtpPass
          ? { user: config.smtpUser, pass: config.smtpPass }
          : undefined,
    })
  : null;

/**
 * Send an email. In 'log' mode (default, and always in local dev) the email
 * is printed to stdout instead of being delivered. Set EMAIL_MODE=smtp plus
 * SMTP_* vars to actually deliver (works with any SMTP provider:
 * Postmark, Resend, Amazon SES, Gmail, ...).
 */
export async function sendEmail(args: SendEmailArgs): Promise<SendEmailResult> {
  if (!transporter) {
    console.log(
      `[email:log] To: ${args.to}\nSubject: ${args.subject}\n` +
        `Attachments: ${(args.attachments ?? []).map((a) => a.filename).join(', ') || 'none'}\n` +
        `---\n${args.text ?? args.html.replace(/<[^>]+>/g, '')}\n---`,
    );
    return { sent: false };
  }

  const info = await transporter.sendMail({
    from: config.emailFrom,
    to: args.to,
    subject: args.subject,
    html: args.html,
    text: args.text,
    attachments: args.attachments?.map((a) => ({
      filename: a.filename,
      content: a.content,
      contentType: a.contentType,
    })),
  });
  return { sent: true, messageId: info.messageId };
}
