"use client";

import { displayLeadName, type LeadCard } from "@/lib/lead-answer";

const SPEC_KEYS = new Set(["quantity", "size", "material", "finish", "artwork"]);

function stateSlug(status: string): string {
  return status.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function LeadTile({ lead }: { lead: LeadCard }) {
  const extraMissing = lead.missing.filter((item) => !SPEC_KEYS.has(item.key));
  return (
    <li className="lead-card">
      <div className="lead-card-head">
        <div className="lead-card-identity">
          <h3 className="lead-card-name">{displayLeadName(lead.name)}</h3>
          {lead.company && <p className="lead-card-company">{lead.company}</p>}
        </div>
        {(lead.status || lead.idle) && (
          <div className="lead-card-meta">
            {lead.status && (
              <span className="lead-status-pill" data-state={stateSlug(lead.status)}>
                {lead.status}
              </span>
            )}
            {lead.idle && <span className="lead-idle">{lead.idle}</span>}
          </div>
        )}
      </div>

      {lead.product && <p className="lead-card-product">{lead.product}</p>}

      {(lead.specs.length > 0 || extraMissing.length > 0) && (
        <dl className="lead-specs">
          {lead.specs.map((spec) => (
            <div className="lead-spec" key={spec.key} data-missing={spec.value === null}>
              <dt>{spec.label}</dt>
              {spec.value === null ? (
                <dd className="lead-missing-chip" title={`Missing ${spec.label.toLowerCase()}`}>
                  missing
                </dd>
              ) : (
                <dd>{spec.value}</dd>
              )}
            </div>
          ))}
          {extraMissing.map((item) => (
            <div className="lead-spec" key={`extra-${item.key}`} data-missing="true">
              <dt>{item.label}</dt>
              <dd className="lead-missing-chip" title={`Missing ${item.label.toLowerCase()}`}>
                missing
              </dd>
            </div>
          ))}
        </dl>
      )}

      {lead.nothingMissing && <p className="lead-nothing-missing">Nothing missing</p>}

      {lead.nextAction && (
        <div className="lead-next">
          <span className="lead-next-label">Next step</span>
          <p className="lead-next-text">{lead.nextAction}</p>
        </div>
      )}

      {lead.extras.map((item, index) => (
        <p className="lead-extra" key={`${item.label}-${index}`}>
          {item.label && <span className="lead-extra-label">{item.label}: </span>}
          {item.value}
        </p>
      ))}

      {lead.reference && <p className="lead-ref">Lead {lead.reference}</p>}
    </li>
  );
}

export function LeadCards({ cards }: { cards: LeadCard[] }) {
  return (
    <ol className="lead-cards" aria-label="Leads">
      {cards.map((lead, index) => (
        <LeadTile key={lead.reference ? `${lead.reference}-${index}` : `lead-${index}`} lead={lead} />
      ))}
    </ol>
  );
}
