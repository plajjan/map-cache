# map-cache
map-cache is a Cisco NSO package providing cached mapping tables for fast
lookups, typically of otherwise slow-to-retrieve live-status data. The data is
kept up to date by continuously running background workers populating the
mappings.

## Typical problem
When working with SNMP data there is commonly a need to lookup values in various
tables. This need largely stems from the limitation in the MIB language that you
cannot have nested lists/tables, so you have to heavily refer in between them.
In addition, MIBs do not offer using natural keys for tables, instead all
indices are integers, requiring another lookup to get the natural "key".

For example, one table refers to ifIndex 3 but all we really care about is that
this maps to the interface name (ifName) 'HundredGigE0/1/2/3'.

NCS makes it pretty easy to do these lookups, you just fetch
/devices/device{FOO}/live-status/IF-MIB/ifTable[ifName='HundredGigE0/1/2/3'] or
look at the index if you want to go the other direction. The problem with such a
lookup is that it doesn't translate well into SNMP, you can't actually ask for
the table row with ifName='HundredGigE0/1/2/3' so instead NCS fetches the entire
table and iterates over each entry trying to find a match. This is slow and if
you want it to be fast, there really is only way to do it and that is to build a
cache, which is precisely what this package is about.


## live-status TTL
What about the TTL for live-status?

Unlike live-status data, which can also be cached for a certain period, the
caching behaves rather differently with map-cache. Once the live-status TTL
expires, that data will be evicted from the cache and subsequently asking for it
will result in NCS going out to the device and fetching the data again. The TTL
is usually quite low so it happens frequently and the net result is that it's
slow. map-cache on the other hand always keeps the cache data, at expiry we
instead go out and refresh the data so there is always data available, it might
just be a little old at times.

map-cache isn't a replacement for live-status - it couldn't be because it's
typically using live-status data - it is a complement for easily caching certain
mappings for fast lookup, thereby reducing what you have to ask for from
live-status through slow polling.

## Solution - map-cache
A map helps you go from e.g. `/IF-MIB/ifTable/ifName` to `/IF-MIB/ifTable/ifIndex`,
i.e. do a lookup from a key to a value. It is perhaps easiest to understand by
looking at the YANG model;

```
module: map-cache
    +--rw map-cache
       +--rw enabled?          boolean
       +--rw worker-threads?   uint16
       +--rw map* [key_xpath value_xpath]
          +--rw key_xpath      string
          +--rw value_xpath    string
          +--rw device* [name]
             +--rw name               string
             +--rw update-interval?   uint32
             +--ro last-poll-stats
             |  +--ro start-timestamp?   yang:date-and-time
             |  +--ro end-timestamp?     yang:date-and-time
             |  +--ro duration?          yang:timeticks
             |  +--ro entries-polled?    uint32
             +--ro map* [k]
                +--ro k    string
                +--ro v?   string
```
                
`/map-cache/map` is a list with an entry for each mapping. The key consists of
two XPaths pointing out the key and values we want to map from and to
respectively. These serve both to identify this mapping but also act as
*configuration* of the map-cache application itself. This type of mapping is
unique per device, thus under each mapping definition, we have a list of
devices, and under each device we will find the actual mapping of table content.

To continue on the example with `/IF-MIB/ifTable/ifName` mapping to
`/IF-MIB/ifTable/ifIndex`, these would be the key_xpath and value_xpath of
`/map-cache/map`. We can find the ifIndex of HundredGigE0/1/2/3 on device FOO
using the following XPath into the map-cache:

```
/map-cache/map{/IF-MIB/ifTable/ifName,/IF-MIB/ifTable/ifIndex}/device{FOO}/map{HundredGigE0/1/2/3}/v
```

Note that the base of the map-cache tree is configuration, that is you write the
mappings you want and for which device you are interested in them. Background
worker thread will then populate this data and you can read out the final map as
operational data.

## Example data

Here's an example showing how we map ifName to ifIndex with data that has been
populated by a background thread. We can also see some stats for how long the
pool took (duration is in hundreds of a second, so about 56 seconds in this
case) etc.

```
admin@ncs> show map-cache | notab
map-cache map /IF-MIB/ifTable/ifEntry/ifName /IF-MIB/ifTable/ifEntry/ifIndex
 device 901-R1-2056
  last-poll-stats start-timestamp 2018-09-28T19:08:05+00:00
  last-poll-stats end-timestamp 2018-09-28T19:09:19+00:00
  last-poll-stats duration 7429
  last-poll-stats entries-polled 179
  map 100GE1/0/0
   v 48
  map 100GE1/0/1
   v 49
  map GigabitEthernet0/0/0
   v 3
  map GigabitEthernet3/0/0
   v 194
  map GigabitEthernet3/0/0.12001
   v 447
```

Or in XML:
```xml
<config xmlns="http://tail-f.com/ns/config/1.0">
  <map-cache xmlns="http://example.com/map-cache">
    <enabled>true</enabled>
    <worker-threads>1</worker-threads>
    <map>
      <key_xpath>/IF-MIB/ifTable/ifEntry/ifName</key_xpath>
      <value_xpath>/IF-MIB/ifTable/ifEntry/ifIndex</value_xpath>
      <device>
        <name>901-R1-2056</name>
        <last-poll-stats>
          <start-timestamp>2018-09-28T19:04:48+00:00</start-timestamp>
          <end-timestamp>2018-09-28T19:05:44+00:00</end-timestamp>
          <duration>5595</duration>
          <entries-polled>179</entries-polled>
        </last-poll-stats>
        <map>
          <k>100GE1/0/0</k>
          <v>48</v>
        </map>
        <map>
          <k>100GE1/0/1</k>
          <v>49</v>
        </map>
        <map>
          <k>GigabitEthernet0/0/0</k>
          <v>3</v>
        </map>
        <map>
          <k>GigabitEthernet3/0/0</k>
          <v>194</v>
        </map>
        <map>
          <k>GigabitEthernet3/0/0.12001</k>
          <v>447</v>
        </map>
        <map>
          <k>GigabitEthernet3/0/0.2000</k>
          <v>449</v>
        </map>
        <map>
          <k>GigabitEthernet3/0/1</k>
          <v>195</v>
        </map>
        <map>
          <k>GigabitEthernet3/0/1.2000</k>
          <v>506</v>
        </map>
        ...snip snip...
      </device>
    </map>
  </map-cache>
</config>
```


## Configuration options
You can enable or disable map-cache through the leaf /map-cache/enabled. Notice
that it can take some time, up to about a minute, for the background threads to
notice that they should not run. Currently running jobs to populate data will
not be canceled.

You can configure the number of background worker threads that will fetch data
through /map-cache/worker-threads. This defaults to 1.


## Internals
There is one thread that periodically adds job to a queue and then there are one
or more worker threads that get jobs from the queue and perform the actual
polling and population of the map-cache.


## TODO / Bugs / Caveats
* the update-interval doesn't work, map-cache will actually work as fast as it
  can, the worker threads goes through the queue, processing all jobs. As soon
  as the queue is empty it will be replenished again by all the mapping tables
  and so the worker threads will then go through it again ad infinitum. This
  means the whole thing is pretty much going to be waiting for fetching data all
  the time, so bound by the speed at which NCS can fetch data.
