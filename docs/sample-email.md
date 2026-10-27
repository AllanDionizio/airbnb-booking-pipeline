# A sample email, side by side

The pipeline reads the emails Airbnb sends to hosts in Portuguese. This page walks through two of them line by line, with an English translation and what the parser takes from each line. Both are synthetic regression fixtures (fictional listing, `Guest <NATO letter>` name, `HMTEST…` code, made-up amounts), shown here exactly as the parser receives them, blank lines left out.

A test ([`tests/regression/test_sample_email_doc.py`](../tests/regression/test_sample_email_doc.py)) checks that every Portuguese line below is still in its fixture and that each parser output matches the fixture's expected output, so this page cannot drift from the emails the tests run on.

## Booking confirmation

Fixture: [`tests/fixtures/confirmations/simple.json`](../tests/fixtures/confirmations/simple.json), received on 2026-05-20.

| Email (pt-BR) | English | Parser |
|---|---|---|
| `Reserva confirmada - Guest Alpha chega em 12 de jun.` | *Subject:* Booking confirmed - Guest Alpha arrives on Jun 12 | Routes the email; `guest_name` |
| `Nova reserva confirmada! Guest Alpha chega em 12 de jun.` | New booking confirmed! Guest Alpha arrives on Jun 12. | — (the name is taken from the subject) |
| `Envie uma mensagem para confirmar os detalhes do check-in ou para dar as boas-vindas.` | Send a message to confirm the check-in details or to say welcome. | — |
| `https://www.airbnb.com.br/rooms/900001?source_impression_id=email` | Link to the listing | — |
| `Loft Aurora 101` | Listing name | `listing_name` |
| `Casa/apto inteiro` | Entire home/apartment | — |
| `Check-in Checkout` | Check-in, Checkout (two columns) | Anchors the dates block |
| `sex., 12 de jun. dom., 14 de jun.` | Fri, Jun 12 · Sun, Jun 14 | `check_in`, `check_out`; the year comes from the email date |
| `15:00 11:00` | Check-in and checkout times | Ends the dates block |
| `HÓSPEDES` | GUESTS | — |
| `2 adultos` | 2 adults | `num_guests` (adults only) |
| `CÓDIGO DE CONFIRMAÇÃO` | CONFIRMATION CODE | — |
| `HMTEST1001` | | `confirmation_code` |
| `O HÓSPEDE PAGOU` | WHAT THE GUEST PAID | — (the guest's side is skipped) |
| `R$240,00 x 2 noites R$480,00` | R$240.00 × 2 nights | — |
| `Taxa de limpeza para estadias curtas R$90,00` | Short-stay cleaning fee | — |
| `Taxa de serviço do hóspede R$67,20` | Guest service fee | — |
| `TOTAL (BRL) R$637,20` | TOTAL (BRL) | — |
| `PAGAMENTO DO ANFITRIÃO` | HOST PAYOUT | Starts the payout block |
| `Preço da acomodação para 2 noites R$480,00` | Accommodation price for 2 nights | `payout_lines.accommodation` |
| `Taxa de limpeza para estadias curtas R$90,00` | Short-stay cleaning fee | `payout_lines.cleaning_fee` |
| `Taxa de serviço do anfitrião (4.0% + IVA) -R$23,94` | Host service fee (4.0% + VAT) | `payout_lines.host_service_fee`, stored as a positive amount |
| `VOCÊ RECEBE R$546,06` | YOU EARN | `host_payout`; the payout lines must add up to it |
| `O processamento do pagamento pode levar algum tempo.` | Payment processing may take some time. | — |
| `CANCELAMENTOS` | CANCELLATIONS | — |
| `Sua política de cancelamento para os hóspedes é Moderada` | Your cancellation policy for guests is Moderate | — |

`R$` amounts use a comma for decimals and a dot for thousands. A payout label the parser does not know stops the email with `PARSE_ERROR` instead of being dropped, because a missing line would change what the host is paid. The parser output:

```json
{
  "confirmation_code": "HMTEST1001",
  "guest_name": "Guest Alpha",
  "check_in": "2026-06-12",
  "check_out": "2026-06-14",
  "num_guests": 2,
  "listing_name": "Loft Aurora 101",
  "currency": "BRL",
  "payout_lines": {
    "accommodation": 480.0,
    "cleaning_fee": 90.0,
    "host_service_fee": 23.94
  },
  "host_payout": 546.06
}
```

## Cancellation with a penalty

Fixture: [`tests/fixtures/cancellations/penalty_compact.json`](../tests/fixtures/cancellations/penalty_compact.json), received on 2026-08-12.

| Email (pt-BR) | English | Parser |
|---|---|---|
| `Cancelado: reserva HMTEST2001 de 4 de set. – 7 de set. de 2026` | *Subject:* Canceled: reservation HMTEST2001, Sep 4 – Sep 7, 2026 | Routes the email; `confirmation_code` |
| `RESERVA CANCELADA` | RESERVATION CANCELED | Routes the email when the subject is missing |
| `https://www.airbnb.com.br/rooms/900002?source_impression_id=email Studio Boreal 202` | Link to the listing, then its name | — |
| `Anúncio n° 900002` | Listing no. 900002 | — |
| `4 de set. – 7 de set., 2 hóspedes` | Sep 4 – Sep 7, 2 guests | — |
| `Infelizmente, o seu hóspede, Guest Tango, precisou cancelar a reserva HMTEST2001 de 4 de set. – 7 de set.. Seu calendário foi atualizado, e essas datas agora estão disponíveis.` | Unfortunately, your guest, Guest Tango, had to cancel reservation HMTEST2001 for Sep 4 – Sep 7. Your calendar has been updated, and these dates are now available. | `confirmation_code` when the subject lacks it |
| `Conforme sua política de cancelamento, seu recebimento foi atualizado para R$315,40.` | Under your cancellation policy, your payout has been updated to R$315.40. | `outcome: penalty`, `penalty_amount`, `currency` |
| `Este pagamento será liberado 24 horas após a data do check-in. É possível visualizar o pagamento atualizado em seu Histórico de Transações.` | This payout will be released 24 hours after the check-in date. You can see the updated payout in your Transaction History. | — |
| `Você também pode emitir o reembolso no valor total para o usuário clicando no botão abaixo.` | You can also issue a full refund to the guest by clicking the button below. | — |
| `Emitir reembolso integral` | Issue full refund | — (a button, not the outcome) |
| `Tem alguma dúvida? Confira mais informações sobre como funciona quando um hóspede cancela.` | Any questions? Learn more about what happens when a guest cancels. | — |

The trap is the button. "Reembolso integral" (full refund) also appears in every email with a penalty, so the parser matches whole sentences that start with "Conforme sua política de cancelamento," and tests the penalty first. A full refund reads "o hóspede recebeu um reembolso integral" (the guest received a full refund) and carries no amount and no currency. The parser output:

```json
{
  "confirmation_code": "HMTEST2001",
  "outcome": "penalty",
  "penalty_amount": 315.4,
  "currency": "BRL"
}
```
