"""GraphQL documents for the Shopify Admin API.

Pinned to SHOPIFY_API_VERSION (default 2026-07). Field/mutation names verified
against shopify.dev Admin GraphQL docs on 2026-10-01; anything not fully
verifiable is flagged in BUILD_NOTES.md.

Conventions:
- Always check BOTH top-level `errors` and per-mutation `userErrors`.
- IDs are GID strings, passed verbatim.
- refundCreate REQUIRES an idempotency key via the @idempotent directive
  (as of API 2026-04). We interpolate a server-generated UUID key.
"""

GET_ORDER_BY_NAME = """
query GetOrderByName($query: String!) {
  orders(first: 1, query: $query) {
    edges {
      node {
        id
        name
        createdAt
        email
        currencyCode
        customer { id firstName lastName email }
        lineItems(first: 50) {
          edges {
            node {
              id
              title
              variantTitle
              sku
              quantity
              variant { id }
              product { id tags }
              discountedUnitPriceSet { shopMoney { amount currencyCode } }
            }
          }
        }
        fulfillments(first: 10) {
          fulfillmentLineItems(first: 50) {
            edges {
              node {
                id
                quantity
                lineItem { id }
              }
            }
          }
        }
      }
    }
  }
}
"""

RETURN_REQUEST = """
mutation ReturnRequest($input: ReturnRequestInput!) {
  returnRequest(input: $input) {
    return { id status }
    userErrors { field message }
  }
}
"""

RETURN_APPROVE_REQUEST = """
mutation ReturnApproveRequest($input: ReturnApproveRequestInput!) {
  returnApproveRequest(input: $input) {
    return { id status }
    userErrors { field message }
  }
}
"""

RETURN_DECLINE_REQUEST = """
mutation ReturnDeclineRequest($input: ReturnDeclineRequestInput!) {
  returnDeclineRequest(input: $input) {
    return { id status }
    userErrors { field message }
  }
}
"""

# NOTE: refundCreate requires an idempotency key as of 2026-04. The key is
# interpolated as a literal (server-generated UUID hex: safe characters only).
REFUND_CREATE = """
mutation RefundCreate($input: RefundInput!) @idempotent(key: "{idempotency_key}") {{
  refundCreate(input: $input) {{
    refund {{ id }}
    order {{ id }}
    userErrors {{ field message }}
  }}
}}
"""

GIFT_CARD_CREATE = """
mutation GiftCardCreate($input: GiftCardCreateInput!) {
  giftCardCreate(input: $input) {
    giftCard {
      id
      code
      balance { amount currencyCode }
    }
    userErrors { field message }
  }
}
"""

DRAFT_ORDER_CREATE = """
mutation DraftOrderCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name invoiceUrl }
    userErrors { field message }
  }
}
"""

DRAFT_ORDER_INVOICE_SEND = """
mutation DraftOrderInvoiceSend($id: ID!) {
  draftOrderInvoiceSend(id: $id) {
    draftOrder { id }
    userErrors { field message }
  }
}
"""

WEBHOOK_SUBSCRIPTION_CREATE = """
mutation WebhookSubscriptionCreate(
  $topic: WebhookSubscriptionTopic!
  $callbackUrl: URL!
) {
  webhookSubscriptionCreate(
    topic: $topic
    webhookSubscription: { callbackUrl: $callbackUrl, format: JSON }
  ) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""

GET_PRODUCT_VARIANTS = """
query GetProductVariants($id: ID!) {
  product(id: $id) {
    variants(first: 50) {
      edges {
        node {
          id
          title
          sku
          availableForSale
          price
        }
      }
    }
  }
}
"""

# -- Shopify Billing API (recurring app subscriptions) --------------------

GET_ACTIVE_SUBSCRIPTIONS = """
query GetActiveSubscriptions {
  currentAppInstallation {
    activeSubscriptions {
      id
      name
      status
      trialDays
      currentPeriodEnd
    }
  }
}
"""

APP_SUBSCRIPTION_CREATE = """
mutation AppSubscriptionCreate(
  $name: String!
  $returnUrl: URL!
  $test: Boolean
  $trialDays: Int
  $lineItems: [AppSubscriptionLineItemInput!]!
) {
  appSubscriptionCreate(
    name: $name
    returnUrl: $returnUrl
    test: $test
    trialDays: $trialDays
    lineItems: $lineItems
  ) {
    appSubscription {
      id
      name
      status
      trialDays
    }
    confirmationUrl
    userErrors { field message }
  }
}
"""

APP_SUBSCRIPTION_CANCEL = """
mutation AppSubscriptionCancel($id: ID!, $prorate: Boolean) {
  appSubscriptionCancel(id: $id, prorate: $prorate) {
    appSubscription { id status }
    userErrors { field message }
  }
}
"""
