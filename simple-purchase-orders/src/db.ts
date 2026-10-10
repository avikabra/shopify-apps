import { PrismaClient } from '@prisma/client';

declare global {
  // eslint-disable-next-line no-var
  var __spoPrisma: PrismaClient | undefined;
}

export const prisma =
  global.__spoPrisma ?? new PrismaClient();

if (process.env.NODE_ENV !== 'production') {
  global.__spoPrisma = prisma;
}
