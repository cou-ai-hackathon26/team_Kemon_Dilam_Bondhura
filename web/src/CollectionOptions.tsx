import {useCurrency} from './Currency';
import {t} from './i18n';
import type {CashEvent, CollectionPlan} from './types';

export function CollectionOptions({plan, events}: {plan: CollectionPlan; events: CashEvent[]}) {
  const {money} = useCurrency();
  if (!plan.first_shortfall_on) return null;

  return <section className="panel" aria-labelledby="collection-heading">
    <div className="panel-heading"><h2 id="collection-heading">{t('Cover the cash gap')}</h2></div>
    <p>{t('The first reserve shortfall is on {date}. The largest gap is {amount}.', {
      date: plan.first_shortfall_on, amount: money(plan.peak_gap_minor)
    })}</p>
    {plan.options.length > 0 ? <>
      <p>{t('Each option shows what happens if one scheduled receipt arrives earlier.')}</p>
      <ul>{plan.options.map(option => {
        const receipt = events.find(event => event.source_record_id === option.source_id);
        return <li key={option.source_id}>
          <strong>{t('Collect {amount} by {date}', {amount: money(option.advance_minor), date: option.collect_by})}</strong>
          <p>{t('From the receipt scheduled for {date}', {date: option.scheduled_on})} · {receipt?.note || option.source_id}</p>
          <p>{option.remaining_gap_minor
            ? t('Remaining reserve gap: {amount}', {amount: money(option.remaining_gap_minor)})
            : t('This would cover the reserve for the full forecast.')}</p>
        </li>;
      })}</ul>
    </> : <p>{t('No single scheduled receipt can reduce the largest gap. Review your cash forecast or compare a loan offer.')}</p>}
  </section>;
}
